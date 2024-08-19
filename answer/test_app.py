import os
import tempfile
import streamlit as st
from streamlit_chat import message
from rag import ChatPDF
from PIL import Image
import asyncio
from dotenv import load_dotenv
from answer import WebRTCClient
import base64
import io
import json

load_dotenv()

st.set_page_config(page_title="ChatPDF")

# WebRTC setup
signal_server_url = os.getenv('SIGNAL_SERVER_URL')
client_id = os.getenv('CLIENT_ID')

if not signal_server_url or not client_id:
    raise ValueError("Environment variables SIGNAL_SERVER_URL and CLIENT_ID must be set")

# Initialize WebRTC client in session state
if 'webrtc_client' not in st.session_state:
    st.session_state.webrtc_client = WebRTCClient(signal_server_url, client_id)

def display_messages():
    st.subheader("Chat")
    for i, (msg, is_user) in enumerate(st.session_state["messages"]):
        message(msg, is_user=is_user, key=str(i))
    st.session_state["thinking_spinner"] = st.empty()

async def process_input():
    if "assistant" in st.session_state and st.session_state["user_input"] and len(st.session_state["user_input"].strip()) > 0:
        user_text = st.session_state["user_input"].strip()

        with st.session_state["thinking_spinner"], st.spinner(f"Thinking"):

            agent_text = await st.session_state.webrtc_client.send_message('user', user_text)


        st.session_state["messages"].append((user_text, True))
        st.session_state["messages"].append((agent_text, False))

        # Analyze the user input for actions
        st.session_state["assistant"].analyze_text_for_actions(user_text)

async def send_file_via_webrtc(file):
    with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(file.getbuffer())
            file_path = tf.name

    with st.session_state["ingestion_spinner"], st.spinner(f"Ingesting {file.name}"):
        if file.type.startswith('image'):
            image = Image.open(file_path)
    # Send message via WebRTC
    await st.session_state.webrtc_client.send_message('upload', image, is_image=True)
    # st.success("File(s) sent successfully!")

def read_and_send_file():
    for file in st.session_state["file_uploader"]:
        st.session_state["ingestion_spinner"].text(f"Sending {file.name} via WebRTC")
        asyncio.run(send_file_via_webrtc(file))

    # st.success("File(s) sent successfully!")
# def read_and_send_file():
#     for file in st.session_state["file_uploader"]:
#         with tempfile.NamedTemporaryFile(delete=False) as tf:
#             tf.write(file.getbuffer())
#             file_path = tf.name

#         with st.session_state["ingestion_spinner"], st.spinner(f"Ingesting {file.name}"):
#             if file.type.startswith('image'):
#                 image = Image.open(file_path)
#                 loop = get_or_create_eventloop()
#                 loop.run_until_complete(st.session_state.webrtc_client.send_message('upload', image, is_image=True))
#             else:
#                 st.session_state["assistant"].ingest(file_path)
        
        # os.remove(file_path)

    # st.success("File(s) ingested successfully!")



async def setup_webrtc():
    if not st.session_state.webrtc_client.peer_connection:
        await st.session_state.webrtc_client.setup_signal()
        await asyncio.sleep(3)
        # await st.session_state.webrtc_client.send_message('chat', "Hello from answer")
        while True:
            await asyncio.sleep(1)

def main():
    if "messages" not in st.session_state:
        st.session_state["messages"] = []

    if "assistant" not in st.session_state:
        st.session_state["assistant"] = ChatPDF()

    st.header("ChatPDF and Image")

    st.subheader("Upload a document or image")
    st.file_uploader(
        "Upload document or image",
        type=["pdf", "png", "jpg", "jpeg"],
        key="file_uploader",
        on_change=read_and_send_file,
        label_visibility="collapsed",
        accept_multiple_files=True,
    )

    st.session_state["ingestion_spinner"] = st.empty()

    display_messages()
    st.text_input("Message", key="user_input", on_change=lambda: asyncio.run(process_input()))

    # Setup WebRTC connection
    if st.session_state.webrtc_client.peer_connection is None:
        st.info("Setting up WebRTC connection...")
        asyncio.run(setup_webrtc())

    else:
        st.success("WebRTC connection established.")

if __name__ == "__main__":
    main()