# Copyright (c) 2022 Dryad Systems
# pylint: disable=wrong-import-position,unspecified-encoding,unused-argument
import os
import time

server_start = time.time()
if os.getenv("BREAK"):
    time.sleep(60 * 60 * 24)
# import nyacomp

import asyncio
import contextlib
import json
import logging
import sys
import typing as t
import uuid

# from pathlib import Path

import aiortc

# import torch
import aiohttp
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription

pc_logger = logging.getLogger("pc")
pc_logger.setLevel("DEBUG")
pcs = set()

logging.getLogger().setLevel("DEBUG")


class Counter:
    def __init__(self):
        self.count = 0

    @contextlib.contextmanager
    def start(self):
        self.count += 1
        try:
            yield self
        finally:
            self.count -= 1


class Live:
    html = open("index.html").read()
    in_progress = Counter()

    def __init__(self) -> None:
        # token = os.getenv("HF_TOKEN")
        # args: dict = {"use_auth_token": token} if token else {"local_files_only": True}
        # self.txt_pipe = nyacomp.load_compressed(Path("model/boneless_sd.pth"))

        from predict import Predictor as Llama

        self.llama = Llama()
        # self.llama.setup()

        self.connections = set()

    async def generate(self, params: dict) -> t.AsyncIterator[str]:
        start = time.time()
        stream = self.llama.async_predict(**params["input"])
        token_count = 0
        with self.in_progress.start():
            while True:
                tok_start = time.time()
                # while-next() seems clearer than for-in here
                tok = await anext(stream, None)
                if tok is None:
                    break
                token_count += 1
                now = time.time()
                resp = {
                    "text": tok,
                    "token_gen_latency": round((now - start) * 1000)
                    "gen_time": round((now - tok_start) * 1000),
                    "id": params.get("id"),
                    "idx": token_count,
                    "batch_size": self.in_progress.count,
                }
                yield json.dumps(resp)
        tail = {
            "status": "done",
            "id": params.get("id"),
            "batch_size": self.in_progress.count,
        }
        yield json.dumps(tail)
        logging.info(f"finished generating in {time.time() - start:.3f}")

    async def index(self, req: web.Request) -> web.Response:
        return web.Response(body=self.html, content_type="text/html")

    async def js(self, req: web.Request) -> web.Response:
        return web.Response(
            body=open("client.js").read(),
            content_type="application/javascript",
            headers={"Cache-Control": "No-Cache"},
        )

    async def handle_ws(self, request: web.Request) -> web.Response:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        logging.info("ws connected")
        self.connections.add(ws)
        gen = None
        try: 
            async for message in ws:
                logging.info(message)
                if isinstance(message.data, str) and message.data.startswith("ping"):
                    await ws.send_str("pong" + message.data[4:])
                else:
                    # async with generate_lock:
                    gen = self.generate(json.loads(message.data))
                    async for item in gen:
                        await ws.send_str(item)
            gen = None
        finally:
            if gen:
                gen.acancel()

        logging.info("websocket disconnected")
        self.connections.discard(ws)
        return ws

    async def offer(self, request: web.Request) -> web.Response:
        logging.info("handling offer")
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        pc = RTCPeerConnection()
        pc_id = f"PeerConnection({uuid.uuid4()}"
        pcs.add(pc)

        def log_info(msg: str, *args: t.Any) -> None:
            pc_logger.info(pc_id + " " + msg, *args)

        log_info("Created for %s", request.remote)

        @pc.on("datachannel")
        def on_datachannel(channel: aiortc.rtcdatachannel.RTCDataChannel) -> None:
            logging.info(type(channel))

            @channel.on("message")
            async def on_message(message) -> None:
                logging.info(message)
                if isinstance(message, str) and message.startswith("ping"):
                    channel.send("pong" + message[4:])
                elif isinstance(message, str) and message[0] == "{":
                    async for item in self.generate(json.loads(message)):
                        logging.info("sending token over webrtc")
                        channel.send(item)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            log_info("Connection state is %s", pc.connectionState)
            if pc.connectionState == "failed":
                await pc.close()
                pcs.discard(pc)

        # handle offer
        await pc.setRemoteDescription(offer)

        # send answer
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        data = {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        return web.Response(
            content_type="application/json",
            text=json.dumps(data),
        )

    async def on_startup(self, app: web.Application) -> None:
        launched = os.getenv("START")
        await self.llama.async_setup()
        self.cs = cs = aiohttp.ClientSession()
        if launched:
            msg = f"wordmirror started {int(server_start - int(launched))}s after launch, ready {time.time() - server_start:.3f}s after start"
            await cs.post("https://imogen.fly.dev/admin", data=msg)
        req = await cs.get("https://ipinfo.io", headers={"User-Agent": "curl"})
        self.ipinfo = await req.json()
        if "city" in self.ipinfo and "region" in self.ipinfo:
            loc = ", ".join((self.ipinfo["city"], self.ipinfo["region"]))
            self.html = self.html.replace("<!--$LOC-->", f"location: {loc}")
            logging.info(f"got location: {self.ipinfo}")
        else:
            logging.info(f"couldn't get location: {self.ipinfo}")
        # idle exit needs to be in a task because all on_startups have to exit
        # asyncio.create_task(self.idle_exit())

    last_gen = time.time()

    # async def idle_exit(self) -> None:
    #     pod_id = os.getenv("RUNPOD_POD_ID")
    #     while pod_id:
    #         await asyncio.sleep(20 * 60)
    #         if time.time() - self.last_gen > 3600:
    #             await self.cs.post(
    #                 "https://imogen.fly.dev/admin",
    #                 data="mirror shutting down after 20m inactivity",
    #             )
    #             # TODO: if we don't have a volume, exit instead of suspend
    #             # query = 'mutation {podTerminate(input: {podId: "%s"})}' % pod_id
    #             query = 'mutation {podStop(input: {podId: "%s"})}' % pod_id
    #             await self.cs.post(
    #                 "https://api.runpod.io/graphql",
    #                 params={"api_key": os.getenv("RUNPOD_API_KEY")},
    #                 json={"query": query},
    #                 headers={"Content-Type": "application/json"},
    #             )
    #             sys.exit()

    async def on_shutdown(self, app: web.Application) -> None:
        # close peer connections
        coros = [pc.close() for pc in pcs]
        await asyncio.gather(*coros)
        pcs.clear()

    async def handle_endpoint(self, request: web.Request) -> web.Response:
        params = await request.json()
        if "input" not in params:
            return web.json_response({"error": "invalid input"}, code=400)
        start = time.time()
        # [item  async for item in self.generate(params["input"])]
        output = " ".join(list(self.llama.predict(**params["input"])))
        latency = round(time.time() - start, 3)
        logging.info(f"handling endpoint took {latency}")
        resp = {"output": output, "latency": latency}
        return web.json_response(resp)

    # async def ws_only(self, req: web.Request) -> web.Response:
    #     return web.FileResponse("./ws-only.html")

    # async def next_index(self, req: web.Request) -> web.Response:
    #     return web.FileResponse("/app/next/index.html")

    async def conn_count(self, req: web.Request) -> web.Response:
        return web.Response(body=str(len(pcs) + len(self.connections)))


app = web.Application()
live = Live()
app.on_startup.append(live.on_startup)
app.on_shutdown.append(live.on_shutdown)
app.add_routes(
    [
        web.route("*", "/plain", live.index),
        web.route("*", "/client.js", live.js),
        web.post("/offer", live.offer),
        web.get("/ws", live.handle_ws),
        # web.get("/ws-only", live.ws_only),
        web.post(
            "/",
            live.handle_endpoint,
        ),
        web.route("*", "/", live.index),
        # web.route("*", "/", live.next_index),
        # web.static("/", "/app/next"),
    ]
)

if __name__ == "__main__":
    web.run_app(app, port=8080, host="0.0.0.0")