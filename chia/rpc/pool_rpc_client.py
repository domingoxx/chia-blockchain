import asyncio
from chia.protocols.harvester_protocol import PlotCheckInfo, UploadProofOfSpace
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
    async def get_pool_info(self, pool_key: str, machine_name: str, total_space):
      return await self.fetch(f'{self.api_prefix}api/farmer/pool/info',{
        'pool_key': pool_key,
        'machine_name': machine_name,
        'total_space': total_space
      })

    # 上传挑战证明信息
    async def upload_plot_check(self, machine_name: str, pool_key: str, proofs: List[PlotCheckInfo]):
      uploadProofs = []
      for info in proofs:

        uploadProofs.append({
          'plot_id': info.plot_id.hex(),
          'size': info.size,
          'plot_public_key': bytes(info.plot_public_key).hex(),
          'pool_public_key': bytes(info.pool_public_key).hex() if info.pool_public_key != None else None,
          'pool_contract_puzzle_hash': info.pool_contract_puzzle_hash.hex() if info.pool_contract_puzzle_hash != None else None,
          'plot_local_pk': bytes(info.plot_local_pk).hex(),
          'farmer_public_key': bytes(info.farmer_public_key).hex(),
        })

      return await self.fetch(f"{self.api_prefix}/api/pool/plot/check", {
        'machine_name': machine_name,
        'pool_key': pool_key,
        'proofs': uploadProofs
        
      })
      
    async def upload_proof_of_space(self, proof: UploadProofOfSpace):
      proofs = []
      for (quality_strings, pos) in proof.proofs:
        proofs.append({
          'quality_strings': quality_strings,
          'challenge':pos.challenge,
          'pool_public_key':pos.pool_public_key,
          'pool_contract_puzzle_hash':pos.pool_contract_puzzle_hash,
          'plot_public_key':pos.plot_public_key,
          'size':pos.size,
          'proof':pos.proof,
        })

      return await self.fetch(f"{self.api_prefix}/api/pool/poof/upload", {
        'origin_challenge': proof.challenge_hash,
        'origin_sp_hash': proof.sp_hash,
        'signage_point_index': proof.signage_point_index,
        'proofs': proofs
      })