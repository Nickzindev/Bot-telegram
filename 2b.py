import random
import asyncio
from pathlib import Path
from openai import OpenAI
import yaml
import tempfile
import os
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from pydub import AudioSegment
import speech_recognition as sr
import sqlite3
from typing import Optional
import calendar


# Carregar as chaves da API
with open("keys.yaml", "r") as file:
    keys = yaml.safe_load(file)

client = OpenAI(api_key=keys["api_openai"])
telegram_bot_token = keys["api_telegram"]


# Configuração do banco de dados
DB_PATH = "conversas.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico_conversas (
            chat_id TEXT,
            user_id TEXT,
            user_username TEXT,
            mensagem TEXT,
            resposta TEXT,
            data TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_conversation(chat_id: str, user_id: str, user_username: str, mensagem: str, resposta: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO historico_conversas (chat_id, user_id, user_username, mensagem, resposta, data)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (chat_id, user_id, user_username, mensagem, resposta, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_chat_history(chat_id: str) -> list:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_username, mensagem, resposta FROM historico_conversas
        WHERE chat_id = ?
        ORDER BY data
    ''', (chat_id,))
    history = cursor.fetchall()
    conn.close()
    return history

# Adiciona referências ao tempo atual
def add_time_reference(text: str):
    now = datetime.now().strftime("%H:%M")
    return f"{text} (ah, e só pra constar, agora são {now})"

# Função para enviar áudio para o usuário
async def send_audio(update: Update, text_to_speech: str):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_file:
        temp_file_path = temp_file.name
        print(f"Arquivo temporário criado em: {temp_file_path}")

        try:
            response = client.audio.speech.create(
                model="tts-1-hd", # tts-1 é mais rápido, porem com menos qualidade
                voice="nova",
                input=text_to_speech
            )
            response.stream_to_file(temp_file_path)
            print("Áudio gerado com sucesso.")
        except Exception as e:
            print(f"Erro ao gerar o áudio: {e}")
            return

    try:
        with open(temp_file_path, 'rb') as audio_file:
            await update.message.reply_voice(voice=audio_file)
        print("Áudio enviado com sucesso.")
    except Exception as e:
        print(f"Erro ao enviar o áudio: {e}")
    finally:
        os.remove(temp_file_path)
        print("Arquivo temporário removido.")

# Função para dividir o texto de forma natural
def split_text(text: str):
    sentences = text.split('. ')
    if len(sentences) <= 1:
        return [text]

    mid_point = len(sentences) // 2
    first_half = '. '.join(sentences[:mid_point]) + '.'
    second_half = '. '.join(sentences[mid_point:]) + '.'
    return [first_half, second_half]



# Função principal para processar as mensagens
async def process_message(update: Update, context: CallbackContext):
    user_message = update.message.text
    chat_id = str(update.effective_chat.id)
    user = update.message.from_user
    user_id = str(user.id)
    user_username = user.username if user.username else f"{user.first_name} {user.last_name}"

    # Carregar os prompts dos arquivos
    with open("prompt/prompt.txt", "r") as file:
        prompt = file.read()

    with open("prompt/prompt2.txt", "r") as file:
        prompt2 = file.read()

    # Selecionar o prompt com base no ID do usuário, caso assim como eu prefira prompts personalisáveis!
    prompt_to_use = prompt if user_id == "SEU_TELEGRAM_ID_AQUI" else prompt2
    prompt_to_use = prompt_to_use.replace("{user}", user_username.split(" ")[0].split("_")[0])

    # Adicionar a data e a hora atual
    now = datetime.now()
    date_str = now.strftime("%d/%m/%Y")
    time_str = now.strftime("%H:%M")
    day_of_week = calendar.day_name[now.weekday()]

    # Obter histórico de conversas do chat
    chat_history = get_chat_history(chat_id)

    # Prompt com placeholders para o histórico
    prompt_template = """
    Histórico:
    {historico}

    Data atual = ({data_atual})
    Hora atual = ({hora_atual})
    Dia da semana = ({dia_da_semana})

    Pergunta: {pergunta}
    """

    # Substituir os placeholders no prompt_template
    prompt = prompt_template.format(
        historico="\n".join([f"{user} perguntou: {msg}\nResposta: {resp}" for user, msg, resp in chat_history]),
        data_atual=date_str,
        hora_atual=time_str,
        dia_da_semana=day_of_week,
        pergunta=user_message
    )

    # Concatenar o prompt_to_use e o prompt_template
    prompt = f"{prompt_to_use}\n{prompt}"


    # Enviar a mensagem para o GPT
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_message},
        ]
    )
    gpt_response_text = response.choices[0].message.content.strip()

    if not gpt_response_text:
        await update.message.reply_text("Desculpe, não consegui gerar uma resposta.")
        return

    # Adicionar a resposta ao histórico
    save_conversation(chat_id, user_id, user_username, user_message, gpt_response_text)

    # Dividir a mensagem
    text_parts = split_text(gpt_response_text)

    # Sorteia a forma da resposta: 0 = texto, 1 = áudio, 2 = combinação
    response_type = random.choice([0, 1, 2, 3])

    if response_type == 0:
        # Apenas resposta em texto
        for part in text_parts:
            await update.message.reply_text(part)

    elif response_type == 1:
        # Apenas resposta em áudio
        await send_audio(update, gpt_response_text)

    elif response_type == 2:
        # Dividir a resposta entre texto e áudio
        text_part = text_parts[0]
        audio_part = text_parts[1] if len(text_parts) > 1 else ""

        # Enviar a parte do texto
        await update.message.reply_text(text_part)

        # Enviar a parte do áudio
        if audio_part:
            await send_audio(update, audio_part)

    elif response_type == 3:
        # Variar entre duas mensagens de texto, duas de áudio
        if random.choice([True, False]):
            # Enviar duas mensagens de texto
            for part in text_parts:
                await update.message.reply_text(part)
        else:
            # Enviar duas mensagens de áudio
            for part in text_parts:
                if part:
                    await send_audio(update, part)

# Função para iniciar o bot com uma mensagem de boas-vindas
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Olá! Envie-me uma mensagem e eu vou responder com texto e áudio!")

# Handler para processar o áudio enviado
async def handle_audio(update: Update, context: CallbackContext, *args):
    chat_id = str(update.effective_chat.id)
    user = update.message.from_user
    user_id = str(user.id)
    user_username = user.username if user.username else f"{user.first_name} {user.last_name}"
    comando = update.message.text if update.message.text else "Áudio"
    chat_name = update.effective_chat.title if update.effective_chat.title else "Privado"
    r = sr.Recognizer()

    # Obter o arquivo de áudio do Telegram
    audio_file = await update.message.voice.get_file()

    def format_message(title, content):
        return f"{title}: {content}"

    print("\n" + "=" * 50)
    print(format_message("User", user_username))
    print(format_message("Comando", comando))
    print(format_message("Chat", chat_name))
    print("=" * 50 + "\n")

    if not os.path.exists('temp_aud'):
        os.makedirs('temp_aud')

    audio_path = "temp_aud/audio.ogg"
    wav_path = "temp_aud/audio.wav"

    try:
        print(f"Áudio temporário recebido. Salvando em: {audio_path}...")
        await audio_file.download_to_drive(audio_path)
        print("Áudio salvo com sucesso!")
    except Exception as e:
        print(f"Erro ao baixar o áudio: {e}")
        return

    try:
        print("Convertendo áudio para WAV")
        ogg_audio = AudioSegment.from_ogg(audio_path)
        ogg_audio.export(wav_path, format="wav")
        print("Convertido com sucesso!")
    except Exception as e:
        print(f"Erro ao converter o áudio: {e}")
        return

    try:
        print("Lendo áudio")
        with sr.AudioFile(wav_path) as source:
            audio_data = r.record(source)
            audio_text = r.recognize_google(audio_data, language="pt-BR")
            print(f"Texto reconhecido:\n\n{audio_text}\n")
    except Exception as e:
        print(f"Erro ao reconhecer o áudio: {e}")
        return

    # Obter resposta do GPT
    prompt = f"""
    Pergunta: {audio_text}
    """
    
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": prompt},
        ]
    )
    gpt_response_text = response.choices[0].message.content.strip()

    if not gpt_response_text:
        await update.message.reply_text("Desculpe, não consegui gerar uma resposta.")
        return

    # Adicionar a resposta ao histórico
    save_conversation(chat_id, user_id, user_username, audio_text, gpt_response_text)

    # Dividir a mensagem
    text_parts = split_text(gpt_response_text)

    # Sorteia a forma da resposta: 0 = texto, 1 = áudio, 2 = combinação
    response_type = random.choice([0, 1, 2, 3])

    if response_type == 0:
        # Apenas resposta em texto
        for part in text_parts:
            await update.message.reply_text(part)

    elif response_type == 1:
        # Apenas resposta em áudio
        await send_audio(update, gpt_response_text)

    elif response_type == 2:
        # Dividir a resposta entre texto e áudio
        text_part = text_parts[0]
        audio_part = text_parts[1] if len(text_parts) > 1 else ""

        # Enviar a parte do texto
        await update.message.reply_text(text_part)

        # Enviar a parte do áudio
        if audio_part:
            await send_audio(update, audio_part)

    elif response_type == 3:
        # Variar entre duas mensagens de texto, duas de áudio
        if random.choice([True, False]):
            # Enviar duas mensagens de texto
            for part in text_parts:
                await update.message.reply_text(part)
        else:
            # Enviar duas mensagens de áudio
            for part in text_parts:
                if part:
                    await send_audio(update, part)

    # Limpar arquivos temporários
    os.remove(audio_path)
    os.remove(wav_path)

# Inicializar o bot
def main():
    init_db()

    application = Application.builder().token(telegram_bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_audio))

    print("\nBot iniciado!")
    application.run_polling()

if __name__ == "__main__":
    main()