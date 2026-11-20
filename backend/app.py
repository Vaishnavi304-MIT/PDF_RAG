import gradio as gr
from app.main import app as fastapi_app

# Create a minimal placeholder UI
with gr.Blocks() as demo:
    gr.Markdown("# PDF RAG FastAPI Backend is Running")

# Mount your existing FastAPI routes onto the Space
app = gr.mount_gradio_app(fastapi_app, demo, path="/gradio")
