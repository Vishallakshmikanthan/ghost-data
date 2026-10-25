"""Tests for format-specific evidence extraction adapters (log, txt, json, pdf, image)."""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from code.ingestion.document_extractors import (
    ImageExtractor,
    JsonExtractor,
    LogExtractor,
    PdfExtractor,
    TextRecordExtractor,
)
from code.ingestion.evidence_store import EvidenceStore
from code.schemas import EvidenceDocument


class TestDocumentExtractors(unittest.TestCase):
    """Test format-specific extractors on synthetic isolated fixtures."""

    def test_log_extractor(self):
        log_content = (
            '2026-09-15T11:51:08Z level=WARN service=support_platform instance=support-2 '
            'resource_id=support_9553 event=message_pii_scan matched_value="e.varga90@yahoo.com" detector=direct_identifier\n'
            '2026-09-15T11:51:19Z level=INFO service=account_service resource_id=account_4092 '
            'event=profile_lookup_completed rows=0 http_status=404 duration_ms=33\n'
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".log") as f:
            f.write(log_content)
            temp_path = f.name

        try:
            doc = EvidenceDocument(
                file_id="ev_log",
                file_path="evidence/logs/test.log",
                origin="observability",
                captured_at="2026-09-15T12:00:00Z",
                file_type=".log",
            )
            claims = LogExtractor.extract(doc, Path(temp_path))
            self.assertEqual(len(claims), 2)

            # Claim 1: PII scan present
            c1 = claims[0]
            self.assertEqual(c1.resource_id, "support_9553")
            self.assertEqual(c1.system_id, "support_platform")
            self.assertEqual(c1.observed_state, "PRESENT")
            self.assertIn("e.varga90@yahoo.com", c1.identifiers_found)
            self.assertEqual(c1.evidence_id, "ev_log")
            self.assertEqual(c1.extraction_method, "log_kv_parser")

            # Claim 2: 404 absent
            c2 = claims[1]
            self.assertEqual(c2.resource_id, "account_4092")
            self.assertEqual(c2.observed_state, "ABSENT")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_text_record_extractor_pipe_delimited(self):
        txt_content = (
            "CUSTOMER PLATFORM - SHIFT HANDOVER\n"
            "Snapshot: 2026-10-01T12:00:00Z\n\n"
            "- 2026-10-01T12:00:00Z | account_service | account_146 | USER_PROFILE | NOT_FOUND | e.lindberg18@yahoo.com\n"
            "- 2026-10-01T12:00:00Z | analytics_platform | analytics_2034 | ANALYTICS_PROFILE | IDENTIFIABLE | novak.tomasz@icloud.com\n"
            "- 2026-10-01T12:00:00Z | analytics_platform | analytics_6830 | ANALYTICS_PROFILE | ANONYMIZED | no direct identifier\n"
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as f:
            f.write(txt_content)
            temp_path = f.name

        try:
            doc = EvidenceDocument(
                file_id="ev_txt",
                file_path="evidence/records/handover.txt",
                origin="account_service",
                captured_at="2026-10-01T12:00:00Z",
                file_type=".txt",
            )
            claims = TextRecordExtractor.extract(doc, Path(temp_path))
            self.assertEqual(len(claims), 3)

            # Claim 1: NOT_FOUND -> ABSENT
            self.assertEqual(claims[0].resource_id, "account_146")
            self.assertEqual(claims[0].observed_state, "ABSENT")
            self.assertIn("e.lindberg18@yahoo.com", claims[0].identifiers_found)

            # Claim 2: IDENTIFIABLE -> PRESENT
            self.assertEqual(claims[1].resource_id, "analytics_2034")
            self.assertEqual(claims[1].observed_state, "PRESENT")

            # Claim 3: ANONYMIZED
            self.assertEqual(claims[2].resource_id, "analytics_6830")
            self.assertEqual(claims[2].observed_state, "ANONYMIZED")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_json_extractor_snapshots_bundle(self):
        payload = {
            "exported_at": "2026-09-17T12:00:00Z",
            "source": "records_service",
            "snapshots": [
                {
                    "captured_at": "2026-09-09T12:00:00Z",
                    "data_store": "account_service",
                    "collection": "customer_profiles",
                    "records": [
                        {
                            "resource_id": "account_5684",
                            "customer_reference": "user_26",
                            "full_name": "Mariana Almeida",
                            "email": "almeida.mariana@gmail.com",
                            "phone": "+55 21 90180-9087",
                            "account_status": "ACTIVE",
                        }
                    ],
                }
            ],
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as f:
            json.dump(payload, f)
            temp_path = f.name

        try:
            doc = EvidenceDocument(
                file_id="ev_json",
                file_path="evidence/records/bundle.json",
                origin="records_service",
                captured_at="2026-09-17T12:00:00Z",
                file_type=".json",
            )
            claims = JsonExtractor.extract(doc, Path(temp_path))
            self.assertEqual(len(claims), 1)
            c = claims[0]
            self.assertEqual(c.resource_id, "account_5684")
            self.assertEqual(c.system_id, "account_service")
            self.assertEqual(c.observed_state, "PRESENT")
            self.assertIn("user_26", c.identifiers_found)
            self.assertIn("almeida.mariana@gmail.com", c.identifiers_found)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_image_extractor_dimensions_and_ocr_warning(self):
        with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".png") as f:
            temp_path = f.name
            # Create a 200x100 dummy PNG image
            img = Image.new("RGB", (200, 100), color=(73, 109, 137))
            img.save(temp_path, format="PNG")

        try:
            doc = EvidenceDocument(
                file_id="ev_img",
                file_path="evidence/images/test.png",
                origin="admin_portal",
                captured_at="2026-09-01T00:00:00Z",
                file_type=".png",
            )
            claims = ImageExtractor.extract(doc, Path(temp_path))
            # Metadata recorded
            self.assertEqual(doc.metadata["image_size"], (200, 100))
            self.assertEqual(doc.metadata["image_format"], "PNG")
            # If tesseract is not installed, partial status with clear warning
            self.assertIn(doc.extraction_status, ("PARTIAL", "SUCCESS"))
            if doc.extraction_status == "PARTIAL":
                self.assertIn("OCR engine", doc.extraction_warning)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_pdf_extractor_with_real_evidence_file(self):
        # Test on participant-facing PDF if available
        real_pdf = Path("dataset/evidence/documents/account_history_report_20260718.pdf")
        if real_pdf.exists():
            doc = EvidenceDocument(
                file_id="evidence_18",
                file_path="evidence/documents/account_history_report_20260718.pdf",
                origin="account_history_service",
                captured_at="2026-07-18T11:00:00Z",
                file_type=".pdf",
            )
            claims = PdfExtractor.extract(doc, real_pdf)
            self.assertGreater(len(claims), 0)
            self.assertEqual(doc.metadata["page_count"], 2)
            # Statement text should contain user_18 or ingrid.dahl@outlook.com
            found_any = any("ingrid.dahl@outlook.com" in c.identifiers_found or "user_18" in c.identifiers_found for c in claims)
            self.assertTrue(found_any)


if __name__ == "__main__":
    unittest.main()
