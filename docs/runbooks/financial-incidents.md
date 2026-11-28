# Financial Operations & Incident Runbooks

This guide consolidates the operational procedures for monitoring, triaging, and resolving financial incidents on the `englishwithmary.ir` platform.

All production Django commands below assume execution from `/home/deployer/app` using `docker compose -f docker-compose.prod.yml exec web ...`. For local development, run `python manage.py ...` directly inside your activated virtual environment.

---

## Automated Monitoring & Scheduled Sweeps

The daily financial reconciliation task (`wallet.tasks.reconcile_wallet_balances_task`) is scheduled via Celery Beat (`reconcile-wallet-balances-daily`) to run daily at **00:00 UTC** (`crontab(hour=0, minute=0)`).

For every active wallet, it recalculates expected balances directly from immutable `wallet.LedgerEntry` rows:
- **Expected Settled**: `(CREDIT + WAIVER + ADJUSTMENT_CREDIT) - (CONSUME + PAYOUT_SETTLE + ADJUSTMENT_DEBIT)`
- **Expected Reserved**: `(RESERVE + PAYOUT_HOLD) - (RELEASE + CONSUME + PAYOUT_SETTLE + PAYOUT_RELEASE)`

On any mismatch (`cached_settled != expected_settled` or `cached_reserved != expected_reserved`), the task:
1. Logs a `CRITICAL` record in `wallet.services` and `wallet.tasks`.
2. Emits a `fatal` (critical-severity) Sentry event with:
   - **Tags**: `tenant_id`, `wallet_id`, `discrepancy`
   - **Context (`wallet_reconciliation`)**: `wallet_id`, `user_id`, `tenant_id`, `currency`, `cached_settled`, `expected_settled`, `cached_reserved`, `expected_reserved`, `settled_discrepancy`, `reserved_discrepancy`

To run the reconciliation check manually on demand:

```bash
# Production VPS
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c \
  "from wallet.tasks import reconcile_wallet_balances_task; print('Discrepancies:', reconcile_wallet_balances_task())"

# Local development
python manage.py shell -c \
  "from wallet.tasks import reconcile_wallet_balances_task; print('Discrepancies:', reconcile_wallet_balances_task())"
```

---

## Runbook 1: Wallet Reconciliation Mismatch

**Trigger:** Sentry critical/fatal alert (`Wallet balance reconciliation mismatch detected`) or `CRITICAL` log from `reconcile_wallet_balances_task`.

**Important Invariants:**
- Never run raw SQL `UPDATE` or `DELETE` against `wallet_ledgerentry`; PostgreSQL enforces immutability via `raise_immutable_ledger_error()`.
- Freezing a wallet blocks new holds (`reserve_funds`, `verify_and_reserve_payout`) while allowing pre-existing booking holds to settle and incoming credits to post without downtime.

### Step 1: Inspect the Sentry Event
Record `wallet_id`, `tenant_id`, `user_id`, `settled_discrepancy`, and `reserved_discrepancy` from the Sentry event tags and `wallet_reconciliation` context.

### Step 2: Freeze the Affected Wallet Immediately
Prevent new holds (`reserve_funds`, `verify_and_reserve_payout`) on the wallet while investigating:

```bash
# Production VPS
docker compose -f docker-compose.prod.yml exec web python manage.py freeze_wallet --wallet-id=<WALLET_ID>

# Local development
python manage.py freeze_wallet --wallet-id=<WALLET_ID>
```

### Step 3: Audit the Wallet Against Immutable Ledger Entries
Compute expected balances and inspect the exact delta:

```bash
# Production VPS
docker compose -f docker-compose.prod.yml exec web python manage.py audit_wallet --wallet-id=<WALLET_ID>

# Local development
python manage.py audit_wallet --wallet-id=<WALLET_ID>
```

Cross-check recent `LedgerEntry`, `Payment`, `Order`, `Payout`, and `Booking` records in Django Admin (`/admin/`, read-only) or a read-only shell inspection to determine whether the root cause is:
- **Case A (Cache Desync):** Ledger entries are complete and accurate, but `Wallet.settled_balance` or `Wallet.reserved_balance` drifted from `SUM(entries)`. Proceed to **Step 4A**.
- **Case B (Physical Liability Mismatch):** Ledger entries are missing or out of sync with verified external transfers (e.g., an unrecorded deposit or off-platform correction). Proceed to **Step 4B**.

### Step 4A: Repair Cache Desync (Ledger Intact)
Run a dry run first to verify the before/after values, then commit with an active superuser username for the audit trail:

```bash
# 1. Dry run (no DB mutation)
docker compose -f docker-compose.prod.yml exec web python manage.py repair_wallet_cache --wallet-id=<WALLET_ID>

# 2. Commit repair
docker compose -f docker-compose.prod.yml exec web python manage.py repair_wallet_cache \
  --wallet-id=<WALLET_ID> \
  --commit \
  --admin=<SUPERUSER_USERNAME>
```

### Step 4B: Remediate Physical Liability Mismatch (Structured Adjustment)
Inject a formal `ADJUSTMENT_CREDIT` or `ADJUSTMENT_DEBIT` ledger entry with a mandatory incident reference and justification (superuser required):

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py adjust_wallet \
  --wallet-id=<WALLET_ID> \
  --amount=<DECIMAL_AMOUNT> \
  --direction=<credit|debit> \
  --reason="<INCIDENT_REF>: <CLEAR_JUSTIFICATION>" \
  --admin=<SUPERUSER_USERNAME>
```

### Step 5: Re-Audit, Unfreeze, and Verify Clean Sweep
Confirm parity, unfreeze the wallet, and verify zero remaining discrepancies:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py audit_wallet --wallet-id=<WALLET_ID>
docker compose -f docker-compose.prod.yml exec web python manage.py freeze_wallet --wallet-id=<WALLET_ID> --unfreeze
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c \
  "from wallet.tasks import reconcile_wallet_balances_task; assert reconcile_wallet_balances_task() == 0, 'Mismatch remains!'"
```

---

## Runbook 2: Manual Deposit Claim Review & Verification

**Trigger:**
- A student submits an "I Have Paid" claim on a pending top-up `Order` (surfaces in **Teacher Dashboard -> Finance -> Top-Up Claims Queue** and dispatches an alert email/notification), or
- A student reports a deposit that cannot be immediately verified on-chain or via banking apps.

### Step 1: Gather Claim & Order Details
1. Open **Teacher Dashboard -> Finance -> Top-Up Claims Queue** (`/dashboard/finance/`).
2. Note the student name, expected `Order.amount`, `claimed_paid_at`, and `claimed_txhash` (if provided).
3. If needed, inspect the `Order` and `PaymentAddress` in Django Admin (`/admin/payments/order/`) or via read-only shell:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c "
from payments.models import Order
o = Order.objects.select_related('student', 'tenant').get(pk=<ORDER_ID>)
print('Order:', o.id, 'Status:', o.status, 'Amount:', o.amount, o.currency)
print('Claimed at:', o.claimed_at, 'Paid at:', o.claimed_paid_at, 'TXHash:', o.claimed_txhash)
print('Snapshot:', o.payment_instructions_snapshot)
"
```

### Step 2: Independently Verify the External Transfer
- **Crypto (BNB Smart Chain / BEP-20 USDT):**
  1. Look up `claimed_txhash` on BscScan: `https://bscscan.com/tx/<TXHASH>`.
  2. Confirm transaction status is **Success** (`status = 1`) and finalized.
  3. Confirm token contract is **BSC-USD / Tether BEP-20**: `0x55d398326f99059fF775485246999027B3197955`.
  4. Confirm the `To` address matches `o.payment_instructions_snapshot["receiving_address"]` (or the tenant's active `PaymentAddress` at transfer time).
  5. Record the exact net USDT amount transferred.
- **Bank / Offline Transfer:**
  1. Verify receipt reference, sender name, timestamp, and net USD-equivalent amount directly in the teacher's banking/payment app (never rely solely on student screenshots).

### Step 3: Execute Triage Outcome
- **Outcome A — Verified (Exact or Adjusted Amount):**
  1. Click **Credit Deposit** on the claim row (or open **Manual Top-Up** in `/dashboard/finance/`).
  2. Enter the **actual verified net amount** (if the student sent `$48.00` on a `$50.00` order, credit `48.00`; must be `>= tenant.min_topup_amount`).
  3. Enter the verified `txhash` or bank receipt ID as the **Reference ID**. The service prefixes it as `tenant_<tenant_id>:manual_<reference_id>` to prevent accidental double-crediting.
- **Outcome B — Cannot Verify (Wrong Network, Unconfirmed, Missing Reference, or Not Received):**
  1. **Do not credit the wallet.**
  2. Message the student via their in-platform Conversation (or email) requesting the exact transaction hash, block explorer URL, or bank confirmation reference, and remind them deposits must use **BNB Smart Chain (BEP-20) USDT**.
  3. Leave the claim uncredited. Unfulfilled `PENDING` orders expire automatically after 24 hours via `payments.tasks.expire_orders_task` without modifying any wallet or ledger rows.
- **Outcome C — Valid Deposit Below Minimum Top-Up or to a Retired Address:**
  1. Verify custody on BscScan against `PaymentAddress` history (`status='RETIRED'`).
  2. Escalate to platform superuser to either credit via `adjust_wallet` (with incident justification and `txhash` in `--reason`) or coordinate an off-platform return.

---

## Runbook 3: Service, Worker, Broker & RPC Degraded Triage

**Trigger:** Sentry alerts for Celery/email failures, delayed periodic sweeps (`reconcile_wallet_balances_task`, `expire_orders_task`, `expire_payout_tokens_task`, `auto_complete_lessons_task`, `expire_disputes_task`, `activate_pending_tenant_accounts_task`), `/health/` failures, or RPC endpoint timeouts/lag (Phase 2).

### Step 1: Check Application & Container Health
```bash
# Check container statuses
docker compose -f docker-compose.prod.yml ps

# Verify web health endpoint
curl -i https://englishwithmary.ir/health/

# Inspect recent logs for errors
docker compose -f docker-compose.prod.yml logs --tail=100 web celery_worker celery_beat
```

### Step 2: Verify PostgreSQL, Redis Broker & Celery Workers
```bash
# PostgreSQL readiness
docker compose -f docker-compose.prod.yml exec postgres sh -c 'pg_isready -U "$POSTGRES_USER"'

# Redis ping
docker compose -f docker-compose.prod.yml exec redis redis-cli ping

# Celery worker ping
docker compose -f docker-compose.prod.yml exec celery_worker celery -A tutor_booking inspect ping
```

If `celery_worker` or `celery_beat` is unhealthy or stuck, restart only the affected service:

```bash
docker compose -f docker-compose.prod.yml restart celery_worker celery_beat
```

### Step 3: Verify Email Provider (Brevo API) & Sentry Connectivity
If transactional emails (payout confirmations, top-up claim alerts, dispute warnings) are failing with `AnymailAPIError` or timeouts:
1. Check Sentry for `payments.tasks` / `booking.tasks` retry or failure events.
2. Confirm `BREVO_API_KEY` is valid and within quota in the Brevo dashboard.
3. Verify outbound HTTPS connectivity from the `web` / `celery_worker` container if needed.

### Step 4: Verify Blockchain RPC Endpoints (BSC / Phase 2 Readiness)
When checking BSC RPC availability or triaging `BlockchainScanLag > 15 minutes`:
1. Check primary managed RPC quota and rate-limit metrics in the RPC provider dashboard.
2. Probe primary and fallback RPC endpoints directly:

```bash
curl -sS -X POST -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
  "<RPC_URL>"
```

Expected response contains `"result":"0x..."`. If the primary endpoint returns HTTP 429/5xx or times out, reorder or replace the RPC URL in `.env.prod` and restart `web`, `celery_worker`, and `celery_beat`.

### Step 5: Post-Recovery Verification Sweep
After restoring degraded workers, broker, or dependencies, run the idempotent periodic sweeps and confirm reconciliation passes cleanly:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py shell -c "
from payments.tasks import (
    activate_pending_tenant_accounts_task,
    expire_orders_task,
    expire_payout_tokens_task,
)
from wallet.tasks import reconcile_wallet_balances_task

print('Activated addresses:', activate_pending_tenant_accounts_task())
print('Expired orders:', expire_orders_task())
print('Expired payout tokens:', expire_payout_tokens_task())
discrepancies = reconcile_wallet_balances_task()
print('Reconciliation discrepancies:', discrepancies)
assert discrepancies == 0
"
```
