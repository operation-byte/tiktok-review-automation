"""Temporary private desktop and relay shared by both login services."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.request


def control(operation):
    url=os.environ['CONTROL_URL'].rstrip('/')+'/api/session/'+os.environ['LOGIN_SESSION']+'/'+operation
    req=urllib.request.Request(url,headers={'User-Agent':'TikTokReviewControl/1.0','Authorization':'Bearer '+os.environ['CONTROL_TOKEN']},
                              method='GET' if operation=='poll' else 'POST')
    with urllib.request.urlopen(req,timeout=15) as r:
        body=r.read()
        return json.loads(body) if operation=='poll' else None


async def relay(stop):
    from websockets.asyncio.client import connect
    url=os.environ['CONTROL_URL'].replace('https://','wss://').rstrip('/')+'/api/session/'+os.environ['LOGIN_SESSION']+'/agent'
    while not stop.is_set():
        try:
            async with connect(url,additional_headers={'User-Agent':'TikTokReviewControl/1.0','Authorization':'Bearer '+os.environ['CONTROL_TOKEN']},
                               max_size=2**22,ping_interval=20,user_agent_header='TikTokReviewControl/1.0') as ws:
                reader,writer=await asyncio.open_connection('127.0.0.1',5900)
                async def upload():
                    while not stop.is_set():
                        data=await reader.read(65536)
                        if not data:break
                        await ws.send(data)
                async def download():
                    async for data in ws:
                        if not isinstance(data,bytes):raise RuntimeError('Unexpected relay frame')
                        writer.write(data);await writer.drain()
                tasks=[asyncio.create_task(upload()),asyncio.create_task(download())]
                try:
                    await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
                    writer.close();await writer.wait_closed()
        except Exception:
            if not stop.is_set():await asyncio.sleep(0.5)


def wait_until(check,timeout=10):
    # Poll instead of fixed sleeps so the viewer can attach as soon as the display is up.
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if check():return True
        time.sleep(0.1)
    return False


def vnc_listening():
    try:
        with socket.create_connection(('127.0.0.1',5900),timeout=0.2):return True
    except OSError:return False


@contextmanager
def private_desktop():
    os.environ['DISPLAY'] = ':99'
    processes, stop = [], threading.Event()
    try:
        processes.append(subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1440x900x24', '-nolisten', 'tcp'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if not wait_until(lambda: Path('/tmp/.X11-unix/X99').exists()):
            raise RuntimeError('Private display did not start')
        processes.append(subprocess.Popen(['x11vnc', '-display', ':99', '-localhost', '-nopw', '-forever', '-shared', '-rfbport', '5900'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if not wait_until(vnc_listening):
            raise RuntimeError('Private browser relay did not start')
        threading.Thread(target=lambda: asyncio.run(relay(stop)), daemon=True).start()
        yield
    finally:
        stop.set()
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
