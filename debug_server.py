from fastapi.testclient import TestClient
import traceback

try:
    print("Initializing FastAPI application...")
    from app.main import app
    client = TestClient(app)
    
    print("Simulating HTTP GET request to '/'...")
    response = client.get("/")
    print("STATUS CODE:", response.status_code)
    if response.status_code == 500:
        print("\n--- 500 Internal Server Error Response Details ---")
        print(response.text[:3000])
    else:
        print(f"Request succeeded with code {response.status_code}!")
except Exception as e:
    print("\n--- Exception Raised During Startup ---")
    traceback.print_exc()
