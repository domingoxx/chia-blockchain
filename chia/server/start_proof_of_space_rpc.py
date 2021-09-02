import asyncio
import aiohttp
from chia.rpc.rpc_server import start_rpc_server
from chia.rpc.proof_of_space_rpc_api import ProofOfSpaceRpcApi
from chia.rpc.rpc_server import RpcServer
from typing import Any, Callable, Dict, List, Optional
from chia.consensus.default_constants import DEFAULT_CONSTANTS


def start():
  host = '127.0.0.1'
  port = 8550
  loop = asyncio.get_event_loop()
  rpc_api = ProofOfSpaceRpcApi(DEFAULT_CONSTANTS)
  app = aiohttp.web.Application()
  rpc_server = RpcServer(rpc_api, rpc_api.service_name, None, None, None)
  
  http_routes: Dict[str, Callable] = rpc_api.get_routes()

  routes = [aiohttp.web.post(route, rpc_server._wrap_http_handler(func)) for (route, func) in http_routes.items()]

  app.add_routes(routes)
  runner = aiohttp.web.AppRunner(app, access_log=None)
  loop.run_until_complete(runner.setup())
  site = aiohttp.web.TCPSite(runner, host, port, ssl_context=rpc_server.ssl_context)
  loop.run_until_complete(site.start())
  print(f"Http server already running on {host}:{port}")
  loop.run_forever()
  
start()


