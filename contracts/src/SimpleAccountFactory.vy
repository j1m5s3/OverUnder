#pragma version 0.4.3

interface ISimpleAccount:
    def initialize(entryPoint: address, owner: address): nonpayable

implementation: public(address)
entryPoint: public(address)

@deploy
def __init__(implementation: address, entryPoint: address):
    assert implementation != empty(address), "implementation required"
    assert entryPoint != empty(address), "entrypoint required"
    self.implementation = implementation
    self.entryPoint = entryPoint

@internal
@pure
def _salt(owner: address, salt: bytes32) -> bytes32:
    return keccak256(abi_encode(owner, salt))

@internal
@view
def _getAddress(owner: address, salt: bytes32) -> address:
    proxy_salt: bytes32 = self._salt(owner, salt)
    init_code: Bytes[54] = concat(
        x"602d3d8160093d39f3363d3d373d3d3d363d73",
        convert(self.implementation, bytes20),
        x"5af43d82803e903d91602b57fd5bf3",
    )
    digest: bytes32 = keccak256(
        concat(
            x"ff",
            convert(self, bytes20),
            proxy_salt,
            keccak256(init_code),
        )
    )
    return convert(convert(digest, uint256) & convert(max_value(uint160), uint256), address)

@external
@view
def getAddress(owner: address, salt: bytes32) -> address:
    return self._getAddress(owner, salt)

@external
def createAccount(owner: address, salt: bytes32) -> address:
    addr: address = self._getAddress(owner, salt)
    if addr.codesize != 0:
        return addr
    proxy: address = create_minimal_proxy_to(self.implementation, salt=self._salt(owner, salt))
    assert proxy == addr, "create2 mismatch"
    extcall ISimpleAccount(proxy).initialize(self.entryPoint, owner)
    return proxy
