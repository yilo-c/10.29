import sys
sys.path.extend([r"D:\h\3.25\思忆集", r"D:\h\3.25\思忆集\思忆集app"])

from automation_hub.api.main import app

if __name__ == "__main__":
    import uvicorn
    from api.config import settings
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
