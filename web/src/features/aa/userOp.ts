import { api } from "@/shared/api/client";
import {
  concat,
  encodeFunctionData,
  maxUint256,
  pad,
  parseAbi,
  toHex,
  type Hex,
  type PublicClient,
  type WalletClient,
} from "viem";

const EXECUTE_ABI = parseAbi([
  "function execute(address target, uint256 amount, bytes data) returns (bytes)",
]);

const FACTORY_ABI = parseAbi([
  "function getAddress(address owner, bytes32 salt) view returns (address)",
  "function createAccount(address owner, bytes32 salt) returns (address)",
]);

const ENTRYPOINT_ABI = parseAbi([
  "function getNonce(address sender, uint256 key) view returns (uint256)",
  "function getUserOpHash((address sender, uint256 nonce, bytes initCode, bytes callData, bytes32 accountGasLimits, uint256 preVerificationGas, bytes32 gasFees, bytes paymasterAndData, bytes signature) userOp) view returns (bytes32)",
]);

const ZERO_SALT = ("0x" + "00".repeat(32)) as Hex;

export function gaslessConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_PAYMASTER_ADDRESS &&
      process.env.NEXT_PUBLIC_ACCOUNT_FACTORY &&
      process.env.NEXT_PUBLIC_ENTRYPOINT,
  );
}

function packU128(hi: bigint, lo: bigint): Hex {
  return concat([pad(toHex(hi), { size: 16 }), pad(toHex(lo), { size: 16 })]);
}

function encodeExecute(target: `0x${string}`, data: Hex, value = 0n): Hex {
  return encodeFunctionData({
    abi: EXECUTE_ABI,
    functionName: "execute",
    args: [target, value, data],
  });
}

type UserOpBody = {
  sender: string;
  nonce: number;
  initCode: Hex;
  callData: Hex;
  accountGasLimits: Hex;
  preVerificationGas: number;
  gasFees: Hex;
  paymasterAndData: Hex;
  signature: Hex;
};

async function postUserOp(body: UserOpBody): Promise<{ paymasterAndData: Hex; txHash: Hex | null }> {
  const res = await api("/api/v1/aa/userop", { method: "POST", body: JSON.stringify(body) });
  return {
    paymasterAndData: res.paymasterAndData as Hex,
    txHash: (res.txHash as Hex | null) ?? null,
  };
}

export async function accountAddress(
  publicClient: PublicClient,
  owner: `0x${string}`,
): Promise<`0x${string}`> {
  const factory = process.env.NEXT_PUBLIC_ACCOUNT_FACTORY as `0x${string}`;
  return publicClient.readContract({
    address: factory,
    abi: FACTORY_ABI,
    functionName: "getAddress",
    args: [owner, ZERO_SALT],
  });
}

export async function sendSponsoredExecute(args: {
  publicClient: PublicClient;
  walletClient: WalletClient;
  owner: `0x${string}`;
  target: `0x${string}`;
  data: Hex;
  value?: bigint;
}): Promise<Hex | null> {
  const entryPoint = process.env.NEXT_PUBLIC_ENTRYPOINT as `0x${string}`;
  const factory = process.env.NEXT_PUBLIC_ACCOUNT_FACTORY as `0x${string}`;
  const sender = await accountAddress(args.publicClient, args.owner);
  const code = await args.publicClient.getCode({ address: sender });
  let initCode: Hex = "0x";
  if (!code || code === "0x") {
    initCode = concat([
      factory,
      encodeFunctionData({
        abi: FACTORY_ABI,
        functionName: "createAccount",
        args: [args.owner, ZERO_SALT],
      }),
    ]);
  }
  const nonce = await args.publicClient.readContract({
    address: entryPoint,
    abi: ENTRYPOINT_ABI,
    functionName: "getNonce",
    args: [sender, 0n],
  });
  const gasPrice = await args.publicClient.getGasPrice();
  const callData = encodeExecute(args.target, args.data, args.value ?? 0n);
  const accountGasLimits = packU128(100000n, 400000n);
  const gasFees = packU128(gasPrice, gasPrice);
  const base = {
    sender,
    nonce: Number(nonce),
    initCode,
    callData,
    accountGasLimits,
    preVerificationGas: 50000,
    gasFees,
  };
  const stamped = await postUserOp({ ...base, paymasterAndData: "0x", signature: "0x" });
  const opForHash = {
    sender,
    nonce,
    initCode,
    callData,
    accountGasLimits,
    preVerificationGas: 50000n,
    gasFees,
    paymasterAndData: stamped.paymasterAndData,
    signature: "0x" as Hex,
  };
  const userOpHash = await args.publicClient.readContract({
    address: entryPoint,
    abi: ENTRYPOINT_ABI,
    functionName: "getUserOpHash",
    args: [opForHash],
  });
  const signature = await args.walletClient.signMessage({
    account: args.owner,
    message: { raw: userOpHash },
  });
  const submitted = await postUserOp({
    ...base,
    paymasterAndData: stamped.paymasterAndData,
    signature,
  });
  if (!submitted.txHash) {
    throw new Error("bundler did not broadcast");
  }
  await args.publicClient.waitForTransactionReceipt({ hash: submitted.txHash });
  return submitted.txHash;
}
