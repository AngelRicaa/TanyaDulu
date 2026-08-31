const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, downloadMediaMessage } = require("@whiskeysockets/baileys");
const qrcode = require("qrcode-terminal");
const axios = require("axios");

const PYTHON_API_URL = "http://localhost:5000/process";
const PYTHON_IMAGE_API_URL = "http://localhost:5000/process_image";

async function startBot() {
  const { state, saveCreds } = await useMultiFileAuthState("auth_session");

  const sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
  });

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log("Scan QR code ini pakai WhatsApp di HP-mu:");
      qrcode.generate(qr, { small: true });
    }

    if (connection === "close") {
      const shouldReconnect =
        lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
      console.log("Koneksi terputus, reconnect:", shouldReconnect);
      if (shouldReconnect) {
        startBot();
      }
    } else if (connection === "open") {
      console.log("✅ Bot TanyaDulu terhubung ke WhatsApp!");
    }
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("messages.upsert", async ({ messages }) => {
    const msg = messages[0];
    if (!msg.message || msg.key.fromMe) return;

    const sender = msg.key.remoteJid;
    const isImage = !!msg.message.imageMessage;

    if (isImage) {
      await handleImageMessage(sock, msg, sender);
      return;
    }

    const text =
      msg.message.conversation ||
      msg.message.extendedTextMessage?.text ||
      "";

    if (!text) {
      await sock.sendMessage(sender, {
        text: "Maaf, saya baru bisa memproses pesan teks dan gambar untuk saat ini.",
      });
      return;
    }

    try {
      const response = await axios.post(PYTHON_API_URL, {
        sender: sender,
        text: text,
      });
      const reply = response.data.reply;
      await sock.sendMessage(sender, { text: reply });
    } catch (err) {
      console.error("Error memanggil Python API:", err.message);
      await sock.sendMessage(sender, {
        text: "Maaf, ada gangguan di sistem. Coba lagi sebentar ya.",
      });
    }
  });
}

async function handleImageMessage(sock, msg, sender) {
  try {
    const buffer = await downloadMediaMessage(msg, "buffer", {});
    const imageBase64 = buffer.toString("base64");

    await sock.sendMessage(sender, { text: "📸 Gambar diterima, sedang saya baca..." });

    const response = await axios.post(PYTHON_IMAGE_API_URL, {
      sender: sender,
      image_base64: imageBase64,
    });
    const reply = response.data.reply;
    await sock.sendMessage(sender, { text: reply });
  } catch (err) {
    console.error("Error memproses gambar:", err.message);
    await sock.sendMessage(sender, {
      text: "Maaf, gagal memproses gambar. Coba kirim ulang atau ketik info produknya manual.",
    });
  }
}

startBot();
