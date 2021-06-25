import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from blspy import G1Element

import chia.server.ws_connection as ws  # lgtm [py/import-and-import-from]
from chia.consensus.coinbase import create_puzzlehash_for_pk
from chia.consensus.constants import ConsensusConstants
from chia.protocols import farmer_protocol, harvester_protocol
from chia.protocols.protocol_message_types import ProtocolMessageTypes
from chia.server.outbound_message import NodeType, make_msg
from chia.server.ws_connection import WSChiaConnection
from chia.types.blockchain_format.proof_of_space import ProofOfSpace
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.util.bech32m import decode_puzzle_hash
from chia.util.config import load_config, save_config
from chia.util.ints import uint32, uint64
from chia.util.keychain import Keychain
from chia.wallet.derive_keys import master_sk_to_farmer_sk, master_sk_to_pool_sk, master_sk_to_wallet_sk

log = logging.getLogger(__name__)

from chia.rpc.pool_rpc_client import PoolRpcClient

"""
HARVESTER PROTOCOL (FARMER <-> HARVESTER)
"""


class Farmer:
    pool_client: PoolRpcClient
    host: str
    port: str
    machine_name: str
    pool_key: str
    api_prefix: str
    total_space: Any

    def __init__(
        self,
        root_path: Path,
        farmer_config: Dict,
        pool_config: Dict,
        keychain: Keychain,
        consensus_constants: ConsensusConstants,
    ):
        self._root_path = root_path
        self.config = farmer_config
        # Keep track of all sps, keyed on challenge chain signage point hash
        self.sps: Dict[bytes32, List[farmer_protocol.NewSignagePoint]] = {}

        # Keep track of harvester plot identifier (str), target sp index, and PoSpace for each challenge
        self.proofs_of_space: Dict[bytes32, List[Tuple[str, ProofOfSpace]]] = {}

        # Quality string to plot identifier and challenge_hash, for use with harvester.RequestSignatures
        self.quality_str_to_identifiers: Dict[bytes32, Tuple[str, bytes32, bytes32, bytes32]] = {}

        # number of responses to each signage point
        self.number_of_responses: Dict[bytes32, int] = {}

        # A dictionary of keys to time added. These keys refer to keys in the above 4 dictionaries. This is used
        # to periodically clear the memory
        self.cache_add_time: Dict[bytes32, uint64] = {}

        self.cache_clear_task: asyncio.Task
        self.constants = consensus_constants
        self._shut_down = False
        self.server: Any = None
        self.keychain = keychain
        self.state_changed_callback: Optional[Callable] = None
        self.log = log
        all_sks = self.keychain.get_all_private_keys()
        self._private_keys = [master_sk_to_farmer_sk(sk) for sk, _ in all_sks] + [
            master_sk_to_pool_sk(sk) for sk, _ in all_sks
        ]

        if len(self.get_public_keys()) == 0:
            error_str = "No keys exist. Please run 'chia keys generate' or open the UI."
            raise RuntimeError(error_str)

        # This is the farmer configuration
        self.farmer_target_encoded = self.config["xch_target_address"]
        self.farmer_target = decode_puzzle_hash(self.farmer_target_encoded)

        self.pool_public_keys = [G1Element.from_bytes(bytes.fromhex(pk)) for pk in self.config["pool_public_keys"]]

        # This is the pool configuration, which should be moved out to the pool once it exists
        self.pool_target_encoded = pool_config["xch_target_address"]
        self.pool_target = decode_puzzle_hash(self.pool_target_encoded)
        self.pool_sks_map: Dict = {}
        for key in self.get_private_keys():
            self.pool_sks_map[bytes(key.get_g1())] = key

        assert len(self.farmer_target) == 32
        assert len(self.pool_target) == 32
        if len(self.pool_sks_map) == 0:
            error_str = "No keys exist. Please run 'chia keys generate' or open the UI."
            raise RuntimeError(error_str)

        # 矿池配置
        p_config = load_config(self._root_path, 'pool.yaml')
        self.host = p_config['api_host']
        self.port = p_config['api_port']
        self.api_prefix = p_config.get('api_prefix')
        self.machine_name = p_config['name']
        self.pool_key = p_config['pool_key']
        self.total_space = None
        self.pool_client = None
        self.pool_state = None



    async def _start(self):
        self.cache_clear_task = asyncio.create_task(self._periodically_clear_cache_task())
        await self.create_pool_info_task()

    def _close(self):
        self._shut_down = True

    async def _await_closed(self):
        await self.cache_clear_task

    def _set_state_changed_callback(self, callback: Callable):
        self.state_changed_callback = callback

    async def on_connect(self, peer: WSChiaConnection):
        # Sends a handshake to the harvester
        handshake = harvester_protocol.HarvesterHandshake(
            self.get_public_keys(),
            self.pool_public_keys,
        )
        if peer.connection_type is NodeType.HARVESTER:
            msg = make_msg(ProtocolMessageTypes.harvester_handshake, handshake)
            await peer.send_message(msg)

    def set_server(self, server):
        self.server = server

    def state_changed(self, change: str, data: Dict[str, Any]):
        if self.state_changed_callback is not None:
            self.state_changed_callback(change, data)

    def on_disconnect(self, connection: ws.WSChiaConnection):
        self.log.info(f"peer disconnected {connection.get_peer_info()}")
        self.state_changed("close_connection", {})

    def get_public_keys(self):
        return [child_sk.get_g1() for child_sk in self._private_keys]

    def get_private_keys(self):
        return self._private_keys

    def get_reward_targets(self, search_for_private_key: bool) -> Dict:
        if search_for_private_key:
            all_sks = self.keychain.get_all_private_keys()
            stop_searching_for_farmer, stop_searching_for_pool = False, False
            for i in range(500):
                if stop_searching_for_farmer and stop_searching_for_pool and i > 0:
                    break
                for sk, _ in all_sks:
                    ph = create_puzzlehash_for_pk(master_sk_to_wallet_sk(sk, uint32(i)).get_g1())

                    if ph == self.farmer_target:
                        stop_searching_for_farmer = True
                    if ph == self.pool_target:
                        stop_searching_for_pool = True
            return {
                "farmer_target": self.farmer_target_encoded,
                "pool_target": self.pool_target_encoded,
                "have_farmer_sk": stop_searching_for_farmer,
                "have_pool_sk": stop_searching_for_pool,
            }
        return {
            "farmer_target": self.farmer_target_encoded,
            "pool_target": self.pool_target_encoded,
        }

    def set_reward_targets(self, farmer_target_encoded: Optional[str], pool_target_encoded: Optional[str]):
        config = load_config(self._root_path, "config.yaml")
        if farmer_target_encoded is not None:
            self.farmer_target_encoded = farmer_target_encoded
            self.farmer_target = decode_puzzle_hash(farmer_target_encoded)
            config["farmer"]["xch_target_address"] = farmer_target_encoded
        if pool_target_encoded is not None:
            self.pool_target_encoded = pool_target_encoded
            self.pool_target = decode_puzzle_hash(pool_target_encoded)
            config["pool"]["xch_target_address"] = pool_target_encoded
        save_config(self._root_path, "config.yaml", config)

    async def _periodically_clear_cache_task(self):
        time_slept: uint64 = uint64(0)
        while not self._shut_down:
            if time_slept > self.constants.SUB_SLOT_TIME_TARGET:
                now = time.time()
                removed_keys: List[bytes32] = []
                for key, add_time in self.cache_add_time.items():
                    if now - float(add_time) > self.constants.SUB_SLOT_TIME_TARGET * 3:
                        self.sps.pop(key, None)
                        self.proofs_of_space.pop(key, None)
                        self.quality_str_to_identifiers.pop(key, None)
                        self.number_of_responses.pop(key, None)
                        removed_keys.append(key)
                for key in removed_keys:
                    self.cache_add_time.pop(key, None)
                time_slept = uint64(0)
                log.debug(
                    f"Cleared farmer cache. Num sps: {len(self.sps)} {len(self.proofs_of_space)} "
                    f"{len(self.quality_str_to_identifiers)} {len(self.number_of_responses)}"
                )
            time_slept += 1
            await asyncio.sleep(1)




    #### 矿池代码

    def get_pool_config(self):
      config = load_config(self._root_path, 'pool.yaml')
      config['pool_state'] = self.pool_state
      return config
    def set_pool_config(self, name, pool_key):
      config = load_config(self._root_path, 'pool.yaml')
      if name is not None and len(name) > 0:
        config['name'] = name
        self.machine_name = name
      if pool_key is not None and len(pool_key) > 0:
        config['pool_key'] = pool_key
        self.pool_key = pool_key
      save_config(self._root_path, "pool.yaml", config)
      
      asyncio.create_task(self.configure_pool())

    def calculateTotalSpace(self, fileSizeList):
      sizeMap = {
        '25': 0.6,
        '32': 101.4,
        '33': 208.8,
        '34': 429.8,
        '35': 884.1
      }
      total_space = 0
      for k in fileSizeList:
        size = sizeMap.get(str(k))
        total_space = total_space + size
      self.total_space = total_space

      self.log.info(f"计算文件总大小： {self.total_space}GB")


    def getPoolError(self,msg):
        self.log.error(f"获取矿池信息失败，请确保pool_key, api_host, api_port配置正确。msg={msg}")

    async def configure_pool(self):
      try:
        pool_info = await self.pool_client.get_pool_info(self.pool_key, self.machine_name, self.total_space)

        self.set_reward_targets(pool_info['target_address'],pool_info['target_address'])
        self.pool_state = "矿池配置成功。"
      except ValueError as ver:
        (res_json) = ver.args
        message = ''
        if 'message' in res_json:
          message = res_json['message']
        self.pool_state = f"pool_key无效，请检查是否正确。{message}"
      except BaseException as err:
        self.pool_state = f"请求矿池服务器失败。{err}"

    async def create_pool_info_task(self):
      self.pool_client = await PoolRpcClient.create(self.host, self.port)
      self.pool_client.set_api_prefix(self.api_prefix)
      await self.create_challenge_task()

      async def task():
        while True:
          await self.configure_pool()
          await asyncio.sleep(1 * 60)

      asyncio.create_task(task())

    async def create_challenge_task(self):
      async def task():
        # 2分钟后上报算力
        await asyncio.sleep(2 * 60)
        wait_time = 60 * 60
        while True:

          try:
            challenge_info = await self.pool_client.get_challenge(self.pool_key, self.machine_name)
            if challenge_info['success']:
              log.info(f"获得到一个挑战，{challenge_info}")
              await self.post_plot_check(challenge_info['challenge'])
              if "wait_time" in challenge_info:
                wait_time = challenge_info['wait_time']
                if wait_time < 60:
                  wait_time = 60 * 60
            else:
              log.error(f"获得挑战失败，这将影响矿池奖励，result={challenge_info}")
          except BaseException as err:
            log.exception(err)
            log.error("获取挑战失败，这将影响矿池奖励")
          # 一小时上报一次算力
          await asyncio.sleep(wait_time)

      asyncio.create_task(task())

    async def post_plot_check(self, challenge: str):

      broadcast_farmer = harvester_protocol.PostPlotCheck(
          challenge
      )
      msg = make_msg(ProtocolMessageTypes.post_plot_check, broadcast_farmer)
      await self.server.send_to_all([msg], NodeType.HARVESTER)

    async def upload_plot_check(self, proofs: List[harvester_protocol.PlotCheckInfo]):
      await self.pool_client.upload_plot_check(self.machine_name, self.pool_key, proofs)

    async def upload_proof_of_space(self, proof: harvester_protocol.UploadProofOfSpace):
      await self.pool_client.upload_proof_of_space(self.machine_name, self.pool_key, proof)