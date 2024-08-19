from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
import json
import asyncio
import requests
import logging
import aiohttp
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
import time
import aioconsole
from twilio.rest import Client
import os
from dotenv import load_dotenv
import io
import base64
from PIL import Image

load_dotenv()

account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
client = Client(account_sid, auth_token)

# Generate NTS Token
token = client.tokens.create()

# Extract ICE servers from the token response
ice_servers = [RTCIceServer(urls=server["urls"], username=server.get("username"), credential=server.get("credential")) for server in token.ice_servers]

class WebRTCClient:
    def __init__(self, signaling_server_url, id):
        self.SIGNALING_SERVER_URL = signaling_server_url
        self.ID = id
        self.config = RTCConfiguration(iceServers=ice_servers)
        self.peer_connection = None
        self.channels = {}
        self.channels_ready = {
            'chat': asyncio.Event(),
            'keep_alive': asyncio.Event(),
            'response': asyncio.Event(),
            'upload':asyncio.Event(),
            'user':asyncio.Event()
        }
        self.driver = None
        self.response_futures = {}

    async def keep_alive(self):
        while True:
            if self.channels_ready['keep_alive'].is_set():
                try:
                    self.channels['keep_alive'].send("keep-alive")
                except Exception as e:
                    print(f"Error sending keep-alive: {e}")
            await asyncio.sleep(5)

    async def setup_signal(self):
        print("Starting setup")
        await self.create_peer_connection()
        asyncio.create_task(self.periodic_reconnection())

    async def create_peer_connection(self):
        self.peer_connection = RTCPeerConnection(configuration=self.config)
        
        @self.peer_connection.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            print(f"ICE connection state is {self.peer_connection.iceConnectionState}")

        @self.peer_connection.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            print(f"ICE gathering state is {self.peer_connection.iceGatheringState}")

        self.channels['chat'] = self.peer_connection.createDataChannel("chat")
        self.channels['keep_alive'] = self.peer_connection.createDataChannel("keep_alive")
        self.channels['response'] = self.peer_connection.createDataChannel("response")
        self.channels['upload'] = self.peer_connection.createDataChannel("upload")
        self.channels['user'] = self.peer_connection.createDataChannel("user")


        for channel_name, channel in self.channels.items():
            @channel.on("open")
            async def on_open(channel=channel, name=channel_name):
                print(f"Channel {name} opened")
                self.channels_ready[name].set()
                if name == "keep_alive":
                    asyncio.create_task(self.keep_alive())

            @channel.on("message")
            async def on_message(message, name=channel_name):
                print(f"Message received on channel {name}: {message}")
                if name == 'response':
                    try:
                        data = json.loads(message)
                        if data["type"] == "text":
                            print(f"Received text response on channel {name}: {data['data']}")
                            if 'response' in self.response_futures:
                                print(f"Setting result for future on response channel")
                                self.response_futures['response'].set_result(data["data"])
                                del self.response_futures['response']
                            else:
                                print(f"No future found for response channel")
                        # ... (rest of the code)
                    except json.JSONDecodeError:
                        print(f"Received invalid JSON via RTC Datachannel {name}: {message}")
                    except Exception as e:
                        print(f"Error processing message: {e}")
                else:
                    try:
                        data = json.loads(message)
                        if data["type"] == "image":
                            # Decode and save or process the image
                            img_data = base64.b64decode(data["data"])
                            img = Image.open(io.BytesIO(img_data))
                            img.save("received_image.png")
                            print(f"Received an image via RTC Datachannel {name}")
                        elif data["type"] == "text":
                            print(f"Received via RTC Datachannel {name}: {data['data']}")
                        else:
                            print(f"Received unknown data type via RTC Datachannel {name}")
                    except json.JSONDecodeError:
                        print(f"Received invalid JSON via RTC Datachannel {name}: {message}")
                    except Exception as e:
                        print(f"Error processing message: {e}")

        @self.peer_connection.on("datachannel")
        async def on_datachannel(channel):
            print(f"Data channel '{channel.label}' created by remote party")
            self.channels[channel.label] = channel

            @channel.on("open")
            def on_open():
                print(f"Data channel '{channel.label}' is open")
                if channel.label == "keep_alive":
                    asyncio.create_task(self.keep_alive())

            @channel.on("message")
            async def on_message(message):
                print("message received from channel", message)

        await self.wait_for_offer()

    async def wait_for_offer(self):
        try:
            resp = requests.get(self.SIGNALING_SERVER_URL + "/get_offer")
            print(f"Offer request status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                if data["type"] == "offer":
                    rd = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
                    await self.peer_connection.setRemoteDescription(rd)
                    await self.peer_connection.setLocalDescription(await self.peer_connection.createAnswer())

                    message = {
                        "id": self.ID,
                        "sdp": self.peer_connection.localDescription.sdp,
                        "type": self.peer_connection.localDescription.type
                    }
                    r = requests.post(self.SIGNALING_SERVER_URL + '/answer', data=message)
                    print(f"Answer sent, status: {r.status_code}")
            else:
                print(f"Failed to get offer. Status code: {resp.status_code}")
        except Exception as e:
            print(f"Error during signaling: {str(e)}")
            return

    async def periodic_reconnection(self):
        while True:
            await asyncio.sleep(180000)  # Wait for 3 minutes
            print("Reconnecting after 3 minutes...")
            await self.reconnect()

    async def reconnect(self):
        if self.peer_connection:
            await self.peer_connection.close()
        self.channels = {}
        self.channels_ready = {
            'chat': asyncio.Event(),
            'keep_alive': asyncio.Event()
        }
        await self.create_peer_connection()

    async def send_message_to_rasa(self, message):
        url = "http://localhost:5006/webhooks/rest/webhook"
        payload = {
            "sender": "user",
            "message": message
        }
        headers = {
            "Content-Type": "application/json"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                response_json = await response.json()
                if response_json and isinstance(response_json, list) and len(response_json) > 0:
                    return response_json[0].get('text', '')
                return ''

    async def send_message(self, channel_name, message, is_image=False):
        if channel_name not in self.channels:
            print(f"Invalid channel name: {channel_name}")
            return

        if not self.channels_ready[channel_name].is_set():
            print(f"Channel {channel_name} is not ready yet. Please wait.")
            return

        channel = self.channels[channel_name]
        if channel and channel.readyState == "open":
            if is_image:
                # If the message is an image (file path or PIL Image object)
                if isinstance(message, str):
                    # If message is a file path
                    with Image.open(message) as img:
                        buffered = io.BytesIO()
                        img.save(buffered, format="PNG")
                        img_str = base64.b64encode(buffered.getvalue()).decode()
                elif isinstance(message, Image.Image):
                    # If message is a PIL Image object
                    buffered = io.BytesIO()
                    message.save(buffered, format="PNG")
                    img_str = base64.b64encode(buffered.getvalue()).decode()
                else:
                    raise ValueError("Image must be a file path or a PIL Image object")
                
                # Prepare the message with a flag indicating it's an image
                data_to_send = json.dumps({
                    "type": "image",
                    "data": img_str
                })
            else:
                # If it's a regular text message
                data_to_send = json.dumps({
                    "type": "text",
                    "data": message
                })

            # Create a future to wait for the response, but use 'response' as the key
            response_future = asyncio.get_event_loop().create_future()
            self.response_futures['response'] = response_future

            print(f"Sending via RTC Datachannel {channel_name}: {'[IMAGE]' if is_image else message}")
            channel.send(data_to_send)

            # Wait for the response
            try:
                response = await asyncio.wait_for(response_future, timeout=30.0)
                print(f"Response received: {response}")
                return response
            except asyncio.TimeoutError:
                print(f"Timeout waiting for response")
                return None
            finally:
                if 'response' in self.response_futures:
                    del self.response_futures['response']
        else:
            print(f"Channel {channel_name} is not open. Cannot send message.")
            return None