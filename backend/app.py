import asyncio
import base64
import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from groq import Groq
from elevenlabs import ElevenLabs
import io

# Chargement des variables d'environnement
load_dotenv()

# Configuration des clients API (on n'utilise plus OpenAI)
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

# Configuration
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

app = FastAPI(title="IA Téléphonique")

# Système de prompt pour l'IA
SYSTEM_PROMPT = """Tu es un assistant téléphonique professionnel et amical.
Réponds de manière concise et naturelle (2-3 phrases maximum).
Sois chaleureux et serviable."""


class CallSession:
    """Gère une session d'appel téléphonique"""
    
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.conversation_history = []
        self.audio_buffer = bytearray()
        
    async def process_audio(self, audio_data: bytes):
        """Pipeline complet: STT → IA → TTS"""
        try:
            # 1. Speech-to-Text avec Groq Whisper (gratuit)
            print("🎤 Transcription audio avec Groq...")
            audio_file = io.BytesIO(audio_data)
            audio_file.name = "audio.wav"
            
            transcription = groq_client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=("audio.wav", audio_file, "audio/wav"),
                language="fr",
                response_format="json"
            )
            user_text = transcription.text
            print(f"👤 Utilisateur: {user_text}")
            
            # 2. Génération de réponse avec Groq
            print("🤖 Génération réponse IA...")
            self.conversation_history.append({
                "role": "user",
                "content": user_text
            })
            
            chat_completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *self.conversation_history
                ],
                temperature=0.7,
                max_tokens=150
            )
            
            ai_response = chat_completion.choices[0].message.content
            self.conversation_history.append({
                "role": "assistant",
                "content": ai_response
            })
            print(f"🤖 IA: {ai_response}")
            
            # 3. Text-to-Speech avec ElevenLabs
            print("🔊 Génération audio...")
            audio_response = elevenlabs_client.text_to_speech.convert(
                voice_id=ELEVENLABS_VOICE_ID,
                text=ai_response,
                model_id="eleven_multilingual_v2"
            )
            
            # Conversion en base64 pour l'envoi
            audio_bytes = b"".join(audio_response)
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
            
            # Envoi de la réponse
            await self.websocket.send_json({
                "type": "response",
                "transcript": user_text,
                "ai_response": ai_response,
                "audio": audio_base64
            })
            
            print("✅ Réponse envoyée")
            
        except Exception as e:
            print(f"❌ Erreur traitement: {e}")
            await self.websocket.send_json({
                "type": "error",
                "message": str(e)
            })


@app.get("/")
async def root():
    """Page de test WebSocket"""
    html_content = """
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Test IA Téléphonique</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 50px auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 { color: #333; }
            .status {
                padding: 10px;
                margin: 10px 0;
                border-radius: 5px;
                font-weight: bold;
            }
            .connected { background: #d4edda; color: #155724; }
            .disconnected { background: #f8d7da; color: #721c24; }
            button {
                background: #007bff;
                color: white;
                border: none;
                padding: 12px 24px;
                border-radius: 5px;
                cursor: pointer;
                font-size: 16px;
                margin: 5px;
            }
            button:hover { background: #0056b3; }
            button:disabled {
                background: #ccc;
                cursor: not-allowed;
            }
            .log {
                background: #f8f9fa;
                padding: 15px;
                border-radius: 5px;
                margin-top: 20px;
                max-height: 400px;
                overflow-y: auto;
                font-family: monospace;
                font-size: 14px;
            }
            .log-entry {
                margin: 5px 0;
                padding: 5px;
                border-left: 3px solid #007bff;
                padding-left: 10px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Test IA Téléphonique (100% Gratuit)</h1>
            <p>STT: Groq Whisper | IA: Groq Llama | TTS: ElevenLabs</p>
            <div id="status" class="status disconnected">❌ Déconnecté</div>
            
            <button id="connectBtn" onclick="connect()">Connecter WebSocket</button>
            <button id="recordBtn" onclick="startRecording()" disabled>🎤 Enregistrer</button>
            <button id="stopBtn" onclick="stopRecording()" disabled>⏹️ Arrêter</button>
            
            <div class="log">
                <div id="logs"></div>
            </div>
        </div>

        <script>
            let ws = null;
            let mediaRecorder = null;
            let audioChunks = [];

            function log(message, type = 'info') {
                const logs = document.getElementById('logs');
                const entry = document.createElement('div');
                entry.className = 'log-entry';
                entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
                logs.appendChild(entry);
                logs.scrollTop = logs.scrollHeight;
            }

            function connect() {
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${protocol}//${window.location.host}/ws`;
                
                log(`Connexion à ${wsUrl}...`);
                ws = new WebSocket(wsUrl);

                ws.onopen = () => {
                    document.getElementById('status').className = 'status connected';
                    document.getElementById('status').textContent = '✅ Connecté';
                    document.getElementById('connectBtn').disabled = true;
                    document.getElementById('recordBtn').disabled = false;
                    log('WebSocket connecté !');
                };

                ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    if (data.type === 'response') {
                        log(`Vous: ${data.transcript}`);
                        log(`IA: ${data.ai_response}`);
                        // Jouer l'audio reçu
                        const audio = new Audio(`data:audio/mpeg;base64,${data.audio}`);
                        audio.play();
                    } else if (data.type === 'error') {
                        log(`Erreur: ${data.message}`, 'error');
                    }
                };

                ws.onerror = (error) => {
                    log('Erreur WebSocket', 'error');
                };

                ws.onclose = () => {
                    document.getElementById('status').className = 'status disconnected';
                    document.getElementById('status').textContent = '❌ Déconnecté';
                    document.getElementById('connectBtn').disabled = false;
                    document.getElementById('recordBtn').disabled = true;
                    log('WebSocket déconnecté');
                };
            }

            async function startRecording() {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    mediaRecorder = new MediaRecorder(stream);
                    audioChunks = [];

                    mediaRecorder.ondataavailable = (event) => {
                        audioChunks.push(event.data);
                    };

                    mediaRecorder.onstop = async () => {
                        const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
                        const reader = new FileReader();
                        reader.onloadend = () => {
                            const base64Audio = reader.result.split(',')[1];
                            ws.send(JSON.stringify({
                                type: 'audio',
                                data: base64Audio
                            }));
                            log('Audio envoyé pour traitement...');
                        };
                        reader.readAsDataURL(audioBlob);
                    };

                    mediaRecorder.start();
                    document.getElementById('recordBtn').disabled = true;
                    document.getElementById('stopBtn').disabled = false;
                    log('🎤 Enregistrement en cours...');
                } catch (error) {
                    log(`Erreur micro: ${error.message}`, 'error');
                }
            }

            function stopRecording() {
                if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                    mediaRecorder.stop();
                    mediaRecorder.stream.getTracks().forEach(track => track.stop());
                    document.getElementById('recordBtn').disabled = false;
                    document.getElementById('stopBtn').disabled = true;
                    log('⏹️ Enregistrement arrêté');
                }
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Endpoint WebSocket principal"""
    await websocket.accept()
    session = CallSession(websocket)
    print("✅ Nouvelle connexion WebSocket")
    
    try:
        while True:
            # Réception des messages
            message = await websocket.receive_text()
            data = json.loads(message)
            
            if data.get("type") == "audio":
                # Décodage de l'audio base64
                audio_data = base64.b64decode(data["data"])
                await session.process_audio(audio_data)
                
    except WebSocketDisconnect:
        print("❌ Client déconnecté")
    except Exception as e:
        print(f"❌ Erreur WebSocket: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
