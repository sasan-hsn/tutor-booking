# Terms of Service & Financial Policy

**Version:** 2026.1
**Effective Date:** 2026-01-01

---

These Terms of Service & Financial Policy ("Terms") govern your use of the prepaid wallet, lesson booking, cancellation, dispute resolution, refund/payout, and cryptocurrency deposit features on the platform. By accepting these Terms (Version `2026.1`), you confirm that you have read, understood, and agreed to be bound by all provisions below. Accepting the current Terms is required before initiating wallet top-ups or booking flows, and your acceptance—including user, tenant, version, IP address, and timestamp—is permanently recorded for audit compliance.

## 1. Prepaid Balance Nature

- **USD Store Credit Only:** Wallet balances represent **prepaid store credit** denominated strictly in **United States Dollars (USD)** for booking 1-on-1 tutoring lessons with the designated teacher tenant.
- **Non-Custodial & Non-Bank:** Prepaid wallet balances are non-custodial, **do not constitute a bank deposit**, checking or savings account, escrow trust, or regulated financial product, and are not insured by any government or deposit protection scheme.
- **Zero Interest:** Wallet balances earn **zero interest**, dividends, or yield.
- **Sole Redemption Purpose:** Balances are redeemable solely for tutoring services offered on the platform or for approved balance refunds/payouts under Section 6. Prepaid credit is non-transferable between users or tenants.
- **Minimum Top-Up:** Each wallet top-up is subject to the platform minimum deposit amount of **$20.00 USD** (unless a different minimum is configured by the tenant).
- **Available vs. Reserved Balance:** Your `settled_balance` reflects total credited funds, while `reserved_balance` reflects funds temporarily held for pending lesson requests, confirmed bookings, active disputes, or confirmed payout requests. Only your `available_balance` (`settled_balance - reserved_balance`) may be used to book new fee-bearing lessons or request a payout. Free trial lessons (`$0.00`) do not place any wallet hold.

## 2. 24-Hour Cancellation Policy

- **Timely Student Cancellations (More Than 24 Hours Before Start):** When a student cancels a confirmed booking **more than 24 hours** prior to its scheduled lesson start time (`start_at`), the platform immediately releases 100% of the held lesson fee back to the student's available wallet balance with zero penalty.
- **Short-Notice Student Cancellations (Less Than 24 Hours Before Start):** Direct self-cancellation is disabled **less than 24 hours** prior to the scheduled start time (`start_at`) on confirmed lessons. Instead, the student may submit a cancellation request subject to **full fee forfeiture as a late cancellation fee**, compensating the teacher for reserved time. If a short-notice cancellation request remains unanswered when `start_at` arrives, the booking enters `AWAITING_RESOLUTION`; if the teacher accepts the late cancellation, the held fee is consumed (`LATE_CANCEL`), whereas if the lesson is marked not held due to teacher fault, the held fee is released to the student.
- **Unconfirmed Pending Lesson Requests:** Unconfirmed pending lesson requests (`PENDING` status) may be cancelled by the student at any time prior to teacher confirmation with immediate hold release and zero penalty.
- **Teacher Cancellations, Declines & Teacher Fault:** If the teacher declines a pending lesson request, allows a pending request to expire, cancels a confirmed booking at any time, or marks a lesson not held due to teacher absence or teacher-side technical fault, 100% of the held lesson fee is immediately released back to the student's available wallet balance.
- **Voluntary Goodwill Penalty Waiver:** For settlements resulting from a short-notice cancellation (`LATE_CANCEL`) or student absence (`STUDENT_ABSENT`), the teacher may, at their sole discretion, grant a single-use goodwill penalty waiver returning the consumed fee to the student's wallet balance.

## 3. Finality of Lesson Completion

- A lesson marked `COMPLETED` by the teacher is considered **definitively delivered and finally settled**, permanently consuming the held lesson fee in the platform ledger.
- Marking a lesson `COMPLETED` **immediately closes the student dispute eligibility window** for that booking. Once a lesson is marked `COMPLETED`, no dispute may be opened.
- Confirmed lessons that have passed their scheduled end time (`booking.end_at`) without an open dispute or prior manual resolution are automatically marked `COMPLETED` **48 hours** after `booking.end_at`.
- Settlement finality is subject solely to a teacher's voluntary **goodwill penalty waiver** (strictly limited to `LATE_CANCEL` or `STUDENT_ABSENT` bookings) or an audited **administrative adjustment** where warranted by verified operational or accounting error.

## 4. 48-Hour Student Dispute Window

- **Strictly Student-Initiated:** Lesson disputes may be opened **exclusively by the student** when a lesson in `CONFIRMED` status suffered from severe teacher absence or unresolved technical delivery failure.
- **Strict 48-Hour Eligibility Window:** A dispute must be filed strictly within **48 hours** following the scheduled lesson end time (`booking.end_at <= now <= booking.end_at + 48 hours`).
- **Barred Outside Window or After Completion:** Disputes attempted **prior to** `booking.end_at`, **after the 48-hour window** has elapsed, or **after** the teacher has marked the booking `COMPLETED` are strictly barred.
- **Hold Freeze During Review:** Opening a valid dispute transitions the booking to `DISPUTING`, suspends automatic 48-hour lesson completion, and freezes the held funds until the dispute is resolved.

## 5. 7-Day Dispute Auto-Expiration

- Once a student opens a dispute, the teacher is notified to review and resolve the issue (with automated reminders sent on Day 3 and Day 6).
- If the teacher resolves the dispute in the student's favor, 100% of the held lesson fee is immediately released back to the student's available wallet balance. If resolved in the teacher's favor, the held fee is consumed and the booking is marked `COMPLETED`.
- **Automatic Student-Favor Release After 7 Days:** In the event that a student dispute is **not resolved or mutually settled within 7 calendar days** of being opened, the system **automatically resolves the dispute in the student's favor** and auto-releases 100% of the held funds back to the student's available wallet balance.

## 6. Refund / Payout Policy

- **Eligible Funds:** Students may request a payout (refund) only against their **unreserved available wallet balance** (`available_balance`). Funds currently held for pending or confirmed bookings, active disputes, or existing payout holds cannot be withdrawn until released.
- **24-Hour Email Confirmation Token:** Every payout request initiates a two-stage verification workflow. The platform sends a secure, single-use confirmation link to the student's registered email address valid for **24 hours**. Unconfirmed requests expire automatically after 24 hours with no balance change. Clicking the confirmation link re-verifies `available_balance`, places a `PAYOUT_HOLD` for the requested gross amount, and transitions the request to `REQUESTED`.
- **Manual Processing Timelines & Fee Deductions:** Teachers manually review and process verified refund requests. If a `REQUESTED` payout remains unaddressed after **48 hours**, the system dispatches an automated reminder to the teacher; actual transfer completion depends on manual teacher review and external network settlement timelines. Any **blockchain network transfer gas fees or processing costs** incurred to transmit the refund are **deducted from the gross payout amount** (`amount_net = amount_gross - fee_deducted`).
- **Cancellation & Rejection:** A student may cancel a confirmed payout request while it remains in `REQUESTED` status, immediately releasing the `PAYOUT_HOLD` back to `available_balance`. Once the teacher marks a payout as `PROCESSING`, cancellation is prohibited. If a teacher rejects a payout request with an explanatory reason, the `PAYOUT_HOLD` is immediately released back to the student's available balance.

## 7. Cryptocurrency Network Rules

- **Supported Asset & Network Only:** The platform strictly accepts **USDT (Tether)** deposits exclusively on the **BNB Smart Chain (BEP-20)** network.
- **Mandatory Network Confirmation:** Before displaying the teacher's active deposit receiving address or QR code, the student must explicitly confirm that the transfer will be sent in USDT on the BNB Smart Chain (BEP-20) network only.
- **Sole Student Liability for Wrong Networks or Tokens:** Students bear **sole and exclusive liability** for any funds sent over incorrect blockchain networks (including, without limitation, **TRC-20**, **ERC-20**, **Polygon**, or any non-BEP-20 network) or sent in unsupported cryptocurrencies or tokens.
- **Unrecoverable Transfers:** Transactions sent to incorrect contracts, unsupported networks, retired addresses, or wrong destination addresses **cannot be recovered, reversed, or credited** by the platform or the teacher.
- **No Private Key Custody:** The platform and teacher never hold, manage, or request student wallet private keys or recovery phrases. Top-ups are transferred directly to the active teacher receiving address and credited to the student's ledger balance upon verification.
