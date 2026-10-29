from app.pipeline.providers.smtp_provider import SMTPEmailProvider

def test_single_send():
    print("Testing SMTP connection using credentials in .env...")
    provider = SMTPEmailProvider()
    print(f"SMTP Host: {provider.smtp_host}:{provider.smtp_port}")
    print(f"SMTP User: {provider.smtp_user}")
    
    result = provider.send_email(
        to_email="avnimittal900@gmail.com",
        subject="Narayani Outreach System - Seed Test Probe",
        body_text="Hi Deepak,\n\nThis is a test probe from Narayani Distributors B2B Outreach System (Stage 5 Seed Sending).\n\nBest regards,\nAarav Shah\nNarayani Distributors",
        custom_headers={"X-Narayani-Send-Key": "sk_probe_001"},
    )
    print(f"Result Success: {result.success}")
    print(f"Provider Message-ID: {result.provider_message_id}")
    print(f"Error (if any): {result.error}")
    return result

if __name__ == "__main__":
    test_single_send()
