# GoPay OTP URL Hybrid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the GoPay OTP URL flow work through a standalone hybrid middleware that supports local WhatsApp scan mode and external webhook ingestion without changing the existing registration control flow.

**Architecture:** Keep the existing payment hook untouched and continue passing `payment_gopay_otp_url` into the payment engine. Refactor the local OTP middleware into a standalone HTTP relay that can cache the latest OTP, serve `/latest` and `/healthz`, and optionally ingest OTP messages from external sources through `/ingest`.

**Tech Stack:** Node.js built-in `http` server, optional `whatsapp-web.js` + `qrcode-terminal`, existing Python payment hook tests

---

### Task 1: Add regression coverage for the middleware contract

**Files:**
- Create: `scripts/whatsapp_otp_middleware.test.js`
- Test: `scripts/whatsapp_otp_middleware.test.js`

- [ ] **Step 1: Write the failing test**
- [ ] **Step 2: Run `node --test scripts/whatsapp_otp_middleware.test.js` and verify it fails**
- [ ] **Step 3: Implement the middleware exports and HTTP behavior**
- [ ] **Step 4: Re-run `node --test scripts/whatsapp_otp_middleware.test.js` and verify it passes**

### Task 2: Refactor the middleware into a hybrid relay

**Files:**
- Modify: `scripts/whatsapp_otp_middleware.js`

- [ ] **Step 1: Extract OTP parsing and in-memory state helpers**
- [ ] **Step 2: Add `/healthz`, `/latest`, and `/ingest` endpoints using a dependency-light HTTP server**
- [ ] **Step 3: Keep local WhatsApp scan support behind lazy-loaded optional dependencies**
- [ ] **Step 4: Preserve one-time scan persistence with `LocalAuth` storage**

### Task 3: Clarify the settings UI text

**Files:**
- Modify: `frontend/src/pages/Settings.tsx`

- [ ] **Step 1: Update the `payment_gopay_otp_url` placeholder/help text to mention local middleware or webhook relay**
- [ ] **Step 2: Make sure the wording stays backward compatible with existing file-based OTP users**

### Task 4: Run regression verification

**Files:**
- Test: `scripts/whatsapp_otp_middleware.test.js`
- Test: `test_payment_hook.py`

- [ ] **Step 1: Run `node --test scripts/whatsapp_otp_middleware.test.js`**
- [ ] **Step 2: Run `python test_payment_hook.py`**
- [ ] **Step 3: Confirm the middleware contract works and the existing payment hook tests still pass**
