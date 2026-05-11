export async function sendWhatsApp(to: string, message: string): Promise<boolean> {
  const accountSid = process.env.TWILIO_ACCOUNT_SID;
  const authToken = process.env.TWILIO_AUTH_TOKEN;
  const from = process.env.TWILIO_WHATSAPP_FROM ?? 'whatsapp:+14155238886';

  if (!accountSid || !authToken || accountSid.startsWith('AC') === false) {
    console.warn('[WhatsApp] Twilio no configurado, mensaje simulado:', { to, message });
    return true;
  }

  // Normalizar número: agregar prefijo whatsapp: si no lo tiene
  const toFormatted = to.startsWith('whatsapp:') ? to : `whatsapp:${to}`;

  try {
    const credentials = Buffer.from(`${accountSid}:${authToken}`).toString('base64');
    const body = new URLSearchParams({
      From: from,
      To: toFormatted,
      Body: message,
    });

    const res = await fetch(
      `https://api.twilio.com/2010-04-01/Accounts/${accountSid}/Messages.json`,
      {
        method: 'POST',
        headers: {
          Authorization: `Basic ${credentials}`,
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: body.toString(),
      },
    );

    if (!res.ok) {
      const err = await res.text();
      console.error('[WhatsApp] Error Twilio:', err);
      return false;
    }
    return true;
  } catch (err) {
    console.error('[WhatsApp] Error de red:', err);
    return false;
  }
}
