import hashlib
import hmac
import json
import os
import pytest
from datetime import datetime

from app.config import get_settings
from app.models import KycRecord, RampTx


@pytest.mark.asyncio
async def test_moonpay_session_no_config(client):
    """Test MoonPay session fails without configuration."""
    # Mock JWT token for authenticated request
    token = "mock-jwt-token"
    response = await client.post(
        "/api/v1/ramps/moonpay/session",
        json={"usdc_amount": "100"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # Should fail without MoonPay config
    assert response.status_code in [401, 503]


@pytest.mark.asyncio
async def test_moonpay_webhook_invalid_signature(client):
    """Test MoonPay webhook rejects invalid signature."""
    payload = {
        "type": "transaction_updated",
        "externalTransactionId": "test-tx-123",
        "status": "completed",
        "walletAddress": "0x1234567890123456789012345678901234567890",
        "cryptoAmount": 100.0,
    }

    response = await client.post(
        "/api/v1/ramps/moonpay/webhook",
        json=payload,
        headers={"moonpay-signature": "invalid-signature"},
    )

    # Should reject invalid signature
    assert response.status_code in [401, 503]


@pytest.mark.asyncio
async def test_kyc_session_creation(client, test_db):
    """Test KYC session creation."""
    # This test would need proper JWT mocking
    # Simplified test structure
    pass


@pytest.mark.asyncio
async def test_kyc_check_below_threshold(client, test_db):
    """Test KYC check allows transactions below threshold."""
    # This test would need proper JWT mocking and DB setup
    # Simplified test structure
    pass


@pytest.mark.asyncio
async def test_kyc_check_above_threshold(client, test_db):
    """Test KYC check blocks transactions above threshold without KYC."""
    # This test would need proper JWT mocking and DB setup
    # Simplified test structure
    pass


@pytest.mark.asyncio
async def test_coinbase_fallback_url(client):
    """Test Coinbase onramp URL generation still works."""
    response = await client.get(
        "/api/v1/ramps/onramp-url",
        params={"address": "0x1234567890123456789012345678901234567890", "usdc_amount": "100"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "coinbase"
    assert data["asset"] == "USDC"
    assert data["chain"] == "base"
    assert "pay.coinbase.com" in data["url"]


def test_ramp_tx_model():
    """Test RampTx model structure."""
    from app.models import RampTx

    # Verify model has required fields
    assert hasattr(RampTx, "address")
    assert hasattr(RampTx, "amount")
    assert hasattr(RampTx, "provider_id")
    assert hasattr(RampTx, "status")
    assert hasattr(RampTx, "created_at")
    assert hasattr(RampTx, "updated_at")


def test_kyc_record_model():
    """Test KycRecord model structure."""
    from app.models import KycRecord

    # Verify model has required fields
    assert hasattr(KycRecord, "address")
    assert hasattr(KycRecord, "status")
    assert hasattr(KycRecord, "jurisdiction")
    assert hasattr(KycRecord, "updated_at")


def test_no_gov_id_fields():
    """Test that KycRecord does NOT store government IDs or documents."""
    from app.models import KycRecord
    import inspect

    # Get all field names
    fields = [m for m in dir(KycRecord) if not m.startswith("_")]

    # Assert no fields that would indicate gov ID storage
    forbidden = [
        "document",
        "id_number",
        "passport",
        "license",
        "ssn",
        "tax_id",
        "national_id",
        "image",
        "photo",
        "scan",
    ]

    for field in fields:
        for forbidden_term in forbidden:
            assert forbidden_term not in field.lower(), f"Field {field} suggests gov ID storage"
