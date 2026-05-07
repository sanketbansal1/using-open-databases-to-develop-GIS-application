from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import osmnx as ox
import networkx as nx
import warnings
import math
import traceback
from typing import List
from datetime import datetime # NEW: For temporal routing

warnings.filterwarnings('ignore')

app = FastAPI(title="IITK Smart Nav API - Pro")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

graphs = {}
landmarks = {}
explore_data = {
    "food": {},     
    "money": {},    
    "health": {}    
}

# --- NEW: ACADEMIC AREA GEOFENCE ---
# Approximate GPS boundaries of the academic area (Lat/Lng)
ACADEMIC_BBOX = {
    "lat_min": 26.5080, "lat_max": 26.5160,
    "lng_min": 80.2270, "lng_max": 80.2350
}

def is_in_academic_area(lat, lng):
    return (ACADEMIC_BBOX["lat_min"] <= lat <= ACADEMIC_BBOX["lat_max"] and
            ACADEMIC_BBOX["lng_min"] <= lng <= ACADEMIC_BBOX["lng_max"])

def get_bearing(lat1, lon1, lat2, lon2):
    dLon = math.radians(lon2 - lon1)
    y = math.sin(dLon) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - \
        math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dLon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def get_turn_direction(bearing1, bearing2):
    angle = (bearing2 - bearing1 + 360) % 360
    if 45 <= angle <= 135: return "Turn right"
    elif 135 < angle < 225: return "Make a U-turn"
    elif 225 <= angle <= 315: return "Turn left"
    else: return "Continue straight"

@app.on_event("startup")
def load_data():
    global graphs, landmarks, explore_data
    print("🚀 Initializing IITK Routing Engine...")
    try:
        graphs['walk'] = ox.load_graphml("iitk_walk.graphml")
        graphs['drive'] = ox.load_graphml("iitk_drive.graphml")
        
        iitk_coords = (26.5123, 80.2329)
        tags = {'amenity': True, 'building': True, 'leisure': True, 'office': True}
        pois = ox.features_from_point(iitk_coords, dist=1500, tags=tags)
        named_pois = pois.dropna(subset=['name'])
        
        for index, row in named_pois.iterrows():
            centroid = row['geometry'].centroid 
            name = str(row['name'])
            coords = {"lat": centroid.y, "lng": centroid.x}
            landmarks[name] = coords
            
            amenity_type = str(row.get('amenity', '')).lower()
            if amenity_type in ['cafe', 'restaurant', 'fast_food', 'food_court']:
                explore_data["food"][name] = coords
            elif amenity_type in ['atm', 'bank']:
                explore_data["money"][name] = coords
            elif amenity_type in ['clinic', 'hospital', 'pharmacy']:
                explore_data["health"][name] = coords
                
        print("✅ Backend is ready to serve multi-modal routes!")
    except Exception as e:
        print(f"❌ Error during startup: {e}")

class LocationPoint(BaseModel):
    lat: float
    lng: float

class RouteRequest(BaseModel):
    stops: List[LocationPoint]
    mode: str = "walk"

@app.get("/api/landmarks")
def get_landmarks():
    return dict(sorted(landmarks.items()))

@app.get("/api/explore/{category}")
def explore_category(category: str):
    if category in explore_data: return explore_data[category]
    return {}

@app.post("/api/route")
def calculate_route(req: RouteRequest):
    speeds = {"walk": 5.0, "cycle": 15.0, "drive": 25.0}
    mode = req.mode.lower()
    speed_kmh = speeds.get(mode, 5.0)
    active_graph = graphs['drive'] if mode == 'drive' else graphs['walk']
    
    if len(req.stops) < 2: return []

    total_distance_m = 0
    total_time_min = 0
    full_path_coords = []
    all_steps = []
    
    # --- TEMPORAL CHECK: Is it between xx:50 and xx:05? ---
    current_minute = datetime.now().minute
    is_rush_hour = current_minute >= 50 or current_minute <= 5
    route_affected_by_traffic = False

    try:
        for i in range(len(req.stops) - 1):
            s1 = req.stops[i]
            s2 = req.stops[i+1]
            
            start_node = ox.nearest_nodes(active_graph, s1.lng, s1.lat)
            end_node = ox.nearest_nodes(active_graph, s2.lng, s2.lat)
            if start_node == end_node: continue

            route = nx.shortest_path(active_graph, start_node, end_node, weight='length')
            
            # We now calculate distance and time segment by segment to apply the penalty
            segment_distance_m = 0
            segment_time_min = 0
            
            current_street = None
            current_length = 0
            last_bearing = None
            current_instruction = ""
            
            for j in range(len(route) - 1):
                u = route[j]
                v = route[j+1]
                edge_dict = active_graph.get_edge_data(u, v)
                edge_data = min(edge_dict.values(), key=lambda x: x.get('length', float('inf')))
                
                street_name = edge_data.get('name', 'Unnamed Path')
                if isinstance(street_name, list): street_name = street_name[0]
                edge_length = edge_data.get('length', 0)
                
                lat1, lon1 = active_graph.nodes[u]['y'], active_graph.nodes[u]['x']
                lat2, lon2 = active_graph.nodes[v]['y'], active_graph.nodes[v]['x']
                
                # --- THE GEOFENCED TIME PENALTY ---
                # Base time for this tiny piece of road
                edge_time = (edge_length / 1000) / speed_kmh * 60 
                
                # If it's rush hour, user is walking/cycling, AND they are in the academic area:
                if is_rush_hour and mode in ['walk', 'cycle'] and is_in_academic_area(lat1, lon1):
                    edge_time *= 2.5 # Slower due to crowds!
                    route_affected_by_traffic = True
                # ----------------------------------
                
                segment_distance_m += edge_length
                segment_time_min += edge_time
                
                current_bearing = get_bearing(lat1, lon1, lat2, lon2)
                
                if street_name == current_street: current_length += edge_length
                else:
                    if current_street is not None:
                        all_steps.append({"instruction": current_instruction, "distance": round(current_length)})
                        turn = get_turn_direction(last_bearing, current_bearing)
                        current_instruction = f"Continue onto {street_name}" if turn == "Continue straight" else f"{turn} onto {street_name}"
                    else: current_instruction = f"Head along {street_name}"
                    current_street = street_name
                    current_length = edge_length
                last_bearing = current_bearing 
                
            if current_street is not None:
                 all_steps.append({"instruction": current_instruction, "distance": round(current_length)})
                 
            if i < len(req.stops) - 2:
                 all_steps.append({"instruction": f"📍 Arrived at Waypoint {i+1}", "distance": 0})
            
            total_distance_m += segment_distance_m
            total_time_min += segment_time_min
            
            path_coords = [[active_graph.nodes[n]['y'], active_graph.nodes[n]['x']] for n in route]
            if i > 0 and len(path_coords) > 0: path_coords = path_coords[1:] 
            full_path_coords.extend(path_coords)

        if len(full_path_coords) == 0: return []

        return [{
            "id": 0, 
            "distance_meters": round(total_distance_m, 1), 
            "time_minutes": round(total_time_min, 1), 
            "path": full_path_coords, 
            "steps": all_steps,
            "has_traffic": route_affected_by_traffic # Tell the frontend!
        }]
        
    except nx.NetworkXNoPath:
        raise HTTPException(status_code=404, detail=f"No {mode} route found between stops.")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"System Error: {str(e)}")