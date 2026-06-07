import os
import sys
import subprocess
import time
import webbrowser
import signal

def run():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.join(root_dir, "backend")
    frontend_dir = os.path.join(root_dir, "frontend")

    print("==================================================")
    print("      CAN SLIM Trading Bot Orchestrator           ")
    print("==================================================")

    # 1. Install Python requirements
    print("\n[1/4] Installing Python backend requirements...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=backend_dir,
            check=True
        )
        print("Backend requirements installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error installing Python requirements: {e}")
        sys.exit(1)

    # 2. Install Node requirements
    print("\n[2/4] Installing Frontend Node requirements...")
    # Because PowerShell execution policy is restricted, we run via cmd.exe
    try:
        subprocess.run(
            ["cmd", "/c", "npm", "install"],
            cwd=frontend_dir,
            check=True
        )
        print("Frontend node modules installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Error installing Node modules: {e}")
        sys.exit(1)

    # 3. Launch Backend Server
    print("\n[3/4] Starting FastAPI backend server on http://localhost:8000...")
    backend_cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
    backend_process = subprocess.Popen(
        backend_cmd,
        cwd=backend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    # 4. Launch Frontend Server
    print("[4/4] Starting Vite frontend server on http://localhost:5173...")
    frontend_cmd = ["cmd", "/c", "npm", "run", "dev"]
    frontend_process = subprocess.Popen(
        frontend_cmd,
        cwd=frontend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    # Wait a moment and verify processes are running
    time.sleep(3)
    
    if backend_process.poll() is not None:
        print("Backend failed to start. Logs:")
        print(backend_process.stdout.read())
        sys.exit(1)
        
    if frontend_process.poll() is not None:
        print("Frontend failed to start. Logs:")
        print(frontend_process.stdout.read())
        # Clean backend
        backend_process.terminate()
        sys.exit(1)

    print("\n==================================================")
    print("Both servers running successfully!")
    print("- Backend:  http://localhost:8000")
    print("- Frontend: http://localhost:5173")
    print("==================================================")
    
    print("\nOpening web browser...")
    webbrowser.open("http://localhost:5173")
    print("Press Ctrl+C in this console to terminate both servers.")

    # Graceful shutdown handler
    def shutdown_servers(sig, frame):
        print("\nShutting down servers...")
        backend_process.terminate()
        frontend_process.terminate()
        try:
            backend_process.wait(timeout=3)
            frontend_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            backend_process.kill()
            frontend_process.kill()
        print("Done. Goodbye!")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_servers)
    signal.signal(signal.SIGTERM, shutdown_servers)

    # Keep running and print logs periodically
    try:
        while True:
            # Check if processes died
            if backend_process.poll() is not None:
                print("Backend terminated unexpectedly.")
                shutdown_servers(None, None)
            if frontend_process.poll() is not None:
                print("Frontend terminated unexpectedly.")
                shutdown_servers(None, None)
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_servers(None, None)

if __name__ == "__main__":
    run()
