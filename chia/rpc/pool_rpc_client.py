import asyncio
from typing import Dict, List, Optional, Tuple

from chia.rpc.rpc_client import RpcClient
from chia.types.blockchain_format.sized_bytes import bytes32

from blspy import G1Element
from chia.types.blockchain_format.proof_of_space import ProofOfSpace
from chia.util.bech32m import encode_puzzle_hash
from chia.consensus.coinbase import create_puzzlehash_for_pk


class PoolRpcClient(RpcClient):
    
    api_prefix: str

    def set_api_prefix(self, api_prefix: str):
      self.api_prefix = api_prefix

    # 获得挑战
    async def get_challenge(self, pool_key: str, machine_name: str):
      return await self.fetch(f'{self.api_prefix}api/farmer/pool/challenge', {
        'pool_key': pool_key,
        'machine_name': machine_name
      })


    # 检查 apikey， 获得奖励地址， 
    async def get_pool_info(self, pool_key: str):
      return await self.fetch(f'{self.api_prefix}api/farmer/pool/info',{
        'pool_key': pool_key
      })

    # 上传挑战证明信息
    async def upload_plot_check(self, machine_name: str, pool_key: str, proofs: List[Tuple[bytes32, G1Element, G1Element, ProofOfSpace]]):
      uploadProofs = []
      for (quality_str, local_pk, farmer_pk, pos) in proofs:

        uploadProofs.append({
          'plot_id': pos.get_plot_id().hex(),
          'quality': quality_str.hex(),
          'proof': pos.proof.hex(),
          'challenge': pos.challenge.hex(),
          'size': pos.size,
          'plot_public_key': bytes(pos.plot_public_key).hex(),
          'pool_public_key': bytes(pos.pool_public_key).hex() if pos.pool_public_key != None else None,
          'pool_contract_puzzle_hash': pos.pool_contract_puzzle_hash.hex() if pos.pool_contract_puzzle_hash != None else None,
          'plot_local_pk': bytes(local_pk).hex(),
          'farmer_public_key': bytes(farmer_pk).hex(),
        })

      return await self.fetch(f"{self.api_prefix}/api/pool/plot/check", {
        'machine_name': machine_name,
        'pool_key': pool_key,
        'proofs': uploadProofs
        
      })
      