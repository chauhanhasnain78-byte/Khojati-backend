import os
import sys
import traceback

# Add parent directory to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

try:
    from main import app as main_app
    app = main_app
except Exception as e:
    err_msg = traceback.format_exc()
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Khojati Error Diagnostic")

    @app.api_route("/", methods=["GET", "POST"])
    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def catch_all(path: str = ""):
        return JSONResponse(
            status_code=200,
            content={
                "status": "error_during_startup",
                "error": str(e),
                "traceback": err_msg,
                "env_keys": [k for k in os.environ.keys() if "URL" in k or "POSTGRES" in k or "PRISMA" in k],
            },
        )
