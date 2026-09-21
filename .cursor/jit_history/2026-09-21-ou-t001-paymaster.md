# OU-T001 ERC-4337 paymaster

Shipped 2026-09-21. Sponsor approve/split/AMM/vote/cancel UserOps for injected-EOA `SimpleAccount`s. `matchOrders` stays relayer-only. Email/Privy AA users are not created.

## Decisions (do not reopen)

- T001 done. T002 (Privy JWKS) stays open. T003 and T008–T010 stay open. T004–T007 and T011–T013 stay done.
- 84532 uses canonical EntryPoint `0x0000000071727De22E5E9d8BAf0edAc6f37da032`. 31337 uses MockEntryPoint.
- `SimpleAccountFactory` is `addFactory`'d at deploy; `addFactory` stays operator-callable.
- Deny `matchOrders`, `executeBatch`, and `execute` with `value != 0`.
- `validatePaymasterUserOp` returns bytes context (not bytes32). `postOp` is entrypoint-only. USDC fee fail-closed before sponsorship.
- USDC `approve` spenders: ctf, exchange, amm, feeVault, paymaster.
- `POST /aa/userop` two-phase: empty sig stamps v0.7 `paymasterAndData` (≥129 bytes); signed op does not re-stamp.
- Web keeps EOA `writeContract` when AA public envs are unset.
- `scripts/list_chiefs_primary.py` lists `Chiefs vs Broncos: Chiefs win?` only; scores 31–10 final on that id; idempotent.

## Pointers

- [contracts/script/deploy.py : L48-72] — 84532 canonical EP vs 31337 mock; `addFactory`
- [contracts/src/OverUnderPaymaster.vy : L106-108] — operator `addFactory`
- [contracts/src/OverUnderPaymaster.vy : L183-212] — selector/spender allowlist
- [contracts/src/OverUnderPaymaster.vy : L216-253] — unwrap execute; deny batch and nonzero value
- [contracts/src/OverUnderPaymaster.vy : L322-355] — `validatePaymasterUserOp` bytes context
- [contracts/src/OverUnderPaymaster.vy : L358-368] — `postOp` entrypoint-only fee
- [contracts/src/SimpleAccount.vy : L40-59] — `validateUserOp` + `execute`
- [contracts/src/SimpleAccountFactory.vy : L46-53] — `createAccount`
- [contracts/src/MockEntryPoint.vy : L94-120] — anvil `handleOps`
- [backend/app/aa/router.py : L177-238] — two-phase userop
- [backend/app/aa/bundler.py : L82-165] — stamp + verify operator sig
- [web/src/features/aa/userOp.ts : L30-36] — `gaslessConfigured`
- [web/src/features/aa/userOp.ts : L83-155] — sponsored execute
- [web/src/features/trade/AmmSwap.tsx : L181-268] — gasless vs EOA write
- [scripts/list_chiefs_primary.py : L19-19] — question string
- [scripts/list_chiefs_primary.py : L76-119] — create + score on that id

## Stage 4

- contracts `tests/test_paymaster.py`: 14 passed
- backend `tests/test_aa_userop.py` + `tests/test_auth.py`: 9 passed
- Defect: untracked `mobile/assets/deployments/84532.json` still lists MockEntryPoint, not the canonical address
