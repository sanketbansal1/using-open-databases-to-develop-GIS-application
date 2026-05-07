import osmnx as ox

print("🌍 Downloading Map Data for IITK Campus...")
iitk_coords = (26.5123, 80.2329)
# 1500 meters (1.5km) radius around the campus center
dist_meters = 1500 

try:
    print("🚶‍♂️ 1. Downloading Walking/Cycling Network...")
    G_walk = ox.graph_from_point(iitk_coords, dist=dist_meters, network_type='walk')
    ox.save_graphml(G_walk, "iitk_walk.graphml")
    print("✅ Walking Map Saved!")

    print("🚗 2. Downloading Driving Network...")
    G_drive = ox.graph_from_point(iitk_coords, dist=dist_meters, network_type='drive')
    ox.save_graphml(G_drive, "iitk_drive.graphml")
    print("✅ Driving Map Saved!")

    print("🎉 All maps downloaded successfully! You are ready for Step 2.")
    
except Exception as e:
    print(f"❌ Error downloading maps: {e}")