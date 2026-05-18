"""Test viewer WebSocket connection - checks if stream data flows"""
import asyncio
import json
import websockets

async def test_viewer():
    uri = "ws://127.0.0.1:8080/ws/viewer/test_device"
    
    # First check if any clients exist
    import urllib.request
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:8080/clients")
        data = json.loads(resp.read())
        print(f"Clients on server: {list(data.keys())}")
        if not data:
            print("No clients connected. Start a client first.")
            return
        
        device_id = list(data.keys())[0]
        print(f"Testing viewer connection for device: {device_id}")
        
        # Connect viewer WebSocket
        async with websockets.connect(f"ws://127.0.0.1:8080/ws/viewer/{device_id}") as ws:
            print("Viewer WebSocket connected!")
            
            # Try to receive data for 5 seconds
            for i in range(50):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    if isinstance(msg, bytes):
                        print(f"Received {len(msg)} bytes, first byte: {msg[0]}")
                    else:
                        print(f"Received text: {msg[:100]}")
                except asyncio.TimeoutError:
                    print(f"  No data received in 0.5s (iteration {i})")
            
            print("\nTest complete.")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test_viewer())
