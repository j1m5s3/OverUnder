import hashlib
import hmac
import json
import os
import pytest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import get_settings
from app.models import KycRecord, RampTx


@pytest.mark.asyncio
async def test_moonpay_session_enforces_kyc_gate_pending_over_threshold():
    """Test MoonPay session returns 403 when user has pending KYC and is over threshold."""
    from app.ramps.router import moonpay_session, MoonPaySessionRequest
    from app.db import get_db
    from sqlalchemy.ext.asyncio import AsyncSession

    # Mock dependencies
    mock_user = SimpleNamespace(address="0x1234567890123456789012345678901234567890")
    mock_db = AsyncMock(spec=AsyncSession)

    # Mock KYC record with pending status
    kyc_record = MagicMock()
    kyc_record.status = "pending"
    kyc_record.jurisdiction = ""

    # Mock recent transactions that put user over threshold
    tx1 = MagicMock()
    tx1.amount = "300"
    tx1.status = "completed"
    tx1.created_at = datetime.utcnow()

    # Mock DB queries
    async def mock_execute(query):
        result = MagicMock()
        # First call returns KYC record
        if "kyc_records" in str(query):
            result.scalar_one_or_none = MagicMock(return_value=kyc_record)
        # Second call returns recent transactions
        else:
            result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[tx1])))
        return result

    mock_db.execute = mock_execute

    # Request that would push over threshold (300 + 300 = 600 > 500)
    req = MoonPaySessionRequest(usdc_amount="300")

    # Mock settings
    with patch("app.kyc.router.settings") as mock_settings, patch(
        "app.ramps.router.settings"
    ) as mock_ramps_settings:
        mock_ramps_settings.moonpay_api_key = "test_key"
        mock_ramps_settings.moonpay_secret = "test_secret"
        mock_settings.kyc_threshold_usdc = 500.0
        mock_settings.kyc_restricted_jurisdictions = ""

        # Should raise 403
        from fastapi import HTTPException

        try:
            await moonpay_session(req, user=mock_user, db=mock_db)
            assert False, "Expected HTTPException to be raised"
        except HTTPException as e:
            assert e.status_code == 403
            assert "KYC verification required" in e.detail


@pytest.mark.asyncio
async def test_moonpay_session_allows_kyc_pass():
    """Test MoonPay session succeeds when user has KYC pass status."""
    from app.ramps.router import moonpay_session, MoonPaySessionRequest
    from sqlalchemy.ext.asyncio import AsyncSession

    # Mock dependencies
    mock_user = SimpleNamespace(address="0x1234567890123456789012345678901234567890")
    mock_db = AsyncMock(spec=AsyncSession)

    # Mock KYC record with pass status
    kyc_record = MagicMock()
    kyc_record.status = "pass"
    kyc_record.jurisdiction = ""

    # Mock recent transactions
    tx1 = MagicMock()
    tx1.amount = "300"
    tx1.status = "completed"
    tx1.created_at = datetime.utcnow()

    # Mock DB queries
    async def mock_execute(query):
        result = MagicMock()
        if "kyc_records" in str(query):
            result.scalar_one_or_none = MagicMock(return_value=kyc_record)
        else:
            result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[tx1])))
        return result

    mock_db.execute = mock_execute

    # Request that would push over threshold (300 + 300 = 600 > 500)
    req = MoonPaySessionRequest(usdc_amount="300")

    # Mock settings
    with patch("app.kyc.router.settings") as mock_kyc_settings, patch(
        "app.ramps.router.settings"
    ) as mock_ramps_settings:
        mock_kyc_settings.kyc_threshold_usdc = 500.0
        mock_kyc_settings.kyc_restricted_jurisdictions = ""
        mock_ramps_settings.moonpay_api_key = "test_key"
        mock_ramps_settings.moonpay_secret = "test_secret"

        # Should succeed
        result = await moonpay_session(req, user=mock_user, db=mock_db)
        assert result["provider"] == "moonpay"
        assert "url" in result


@pytest.mark.asyncio
async def test_moonpay_session_allows_below_threshold():
    """Test MoonPay session succeeds when amount is below threshold."""
    from app.ramps.router import moonpay_session, MoonPaySessionRequest
    from sqlalchemy.ext.asyncio import AsyncSession

    # Mock dependencies
    mock_user = SimpleNamespace(address="0x1234567890123456789012345678901234567890")
    mock_db = AsyncMock(spec=AsyncSession)

    # No KYC record
    # No recent transactions

    # Mock DB queries
    async def mock_execute(query):
        result = MagicMock()
        if "kyc_records" in str(query):
            result.scalar_one_or_none = MagicMock(return_value=None)
        else:
            result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        return result

    mock_db.execute = mock_execute

    # Request below threshold
    req = MoonPaySessionRequest(usdc_amount="100")

    # Mock settings
    with patch("app.kyc.router.settings") as mock_kyc_settings, patch(
        "app.ramps.router.settings"
    ) as mock_ramps_settings:
        mock_kyc_settings.kyc_threshold_usdc = 500.0
        mock_kyc_settings.kyc_restricted_jurisdictions = ""
        mock_ramps_settings.moonpay_api_key = "test_key"
        mock_ramps_settings.moonpay_secret = "test_secret"

        # Should succeed
        result = await moonpay_session(req, user=mock_user, db=mock_db)
        assert result["provider"] == "moonpay"
        assert "url" in result


@pytest.mark.asyncio
async def test_moonpay_session_enforces_restricted_jurisdiction():
    """Test MoonPay session returns 403 for restricted jurisdiction without KYC pass."""
    from app.ramps.router import moonpay_session, MoonPaySessionRequest
    from sqlalchemy.ext.asyncio import AsyncSession

    # Mock dependencies
    mock_user = SimpleNamespace(address="0x1234567890123456789012345678901234567890")
    mock_db = AsyncMock(spec=AsyncSession)

    # Mock KYC record with restricted jurisdiction and pending status
    kyc_record = MagicMock()
    kyc_record.status = "pending"
    kyc_record.jurisdiction = "US"

    # Mock DB queries
    async def mock_execute(query):
        result = MagicMock()
        if "kyc_records" in str(query):
            result.scalar_one_or_none = MagicMock(return_value=kyc_record)
        else:
            result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        return result

    mock_db.execute = mock_execute

    # Request below threshold but in restricted jurisdiction
    req = MoonPaySessionRequest(usdc_amount="50")

    # Mock settings with US as restricted
    with patch("app.kyc.router.settings") as mock_settings, patch(
        "app.ramps.router.settings"
    ) as mock_ramps_settings:
        mock_ramps_settings.moonpay_api_key = "test_key"
        mock_ramps_settings.moonpay_secret = "test_secret"
        mock_settings.kyc_threshold_usdc = 500.0
        mock_settings.kyc_restricted_jurisdictions = "US,CN"

        # Should raise 403
        from fastapi import HTTPException

        try:
            await moonpay_session(req, user=mock_user, db=mock_db)
            assert False, "Expected HTTPException to be raised"
        except HTTPException as e:
            assert e.status_code == 403
            assert "KYC verification required" in e.detail


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
