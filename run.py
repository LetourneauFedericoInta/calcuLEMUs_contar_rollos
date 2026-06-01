import uvicorn

if __name__ == "__main__":
    print("Starting Log Counting Web Application...")
    print("Point your browser to: http://127.0.0.1:8000")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
