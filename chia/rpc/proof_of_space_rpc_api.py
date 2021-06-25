from chia.consensus.constants import ConsensusConstants
from chia.util.ints import uint8
from typing import Callable, Dict, List

from blspy import G1Element
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.blockchain_format.proof_of_space import ProofOfSpace
from chia.util.byte_types import hexstr_to_bytes

class ProofOfSpaceRpcApi:
    def __init__(self, consensus_constants:ConsensusConstants):
      self.service_name = "proof_of_space"
      self.consensus_constants = consensus_constants
      

    def get_routes(self) -> Dict[str, Callable]:
      return {
          "/verify": self.verify,
      }
    async def verify(self, request: Dict):
      
      origin_challenge_hash: bytes32 = hexstr_to_bytes(request['origin_challenge'])
      origin_sp_hash: bytes32 = hexstr_to_bytes(request['origin_sp_hash'])
      signage_point_index: uint8 = uint8(request['signage_point_index'])

      proofs: List[Dict] = request['proofs']

      plot_id_list  = []

      for proof in proofs:
        challenge = hexstr_to_bytes(proof['challenge'])
        pool_public_key = G1Element.from_bytes(hexstr_to_bytes(proof['pool_public_key'])) if proof['pool_public_key'] != None else None
        pool_contract_puzzle_hash = hexstr_to_bytes(proof['pool_contract_puzzle_hash']) if proof['pool_contract_puzzle_hash'] != None else None
        plot_public_key = G1Element.from_bytes(hexstr_to_bytes(proof['plot_public_key']))
        size = uint8(proof['size'])
        proof_content = hexstr_to_bytes(proof['proof'])
        quality_strings = hexstr_to_bytes(proof['quality_strings'])

        pos = ProofOfSpace(challenge, pool_public_key, pool_contract_puzzle_hash, plot_public_key, size, proof_content)
        quality_str = pos.verify_and_get_quality_string(self.consensus_constants, origin_challenge_hash, origin_sp_hash)
        
        if quality_str == quality_strings:
          plot_id: bytes32 = pos.get_plot_id()
          plot_id_list.append(plot_id.hex())
        else:
          plot_id_list.append(None)
      return {'plot_id_list':plot_id_list}
        
