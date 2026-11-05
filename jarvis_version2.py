"""Jarvis: a small voice/text assistant that uses a local Ollama model with tools."""

import datetime
import json
import os
import random
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote, quote_plus

# ---------------------------------------------------------------------------
# Optional dependencies: Jarvis still starts if any of these are missing.
# ---------------------------------------------------------------------------
try:
    import pyautogui
except ModuleNotFoundError:
    pyautogui = None

try:
    import pyjokes
except ModuleNotFoundError:
    pyjokes = None

try:
    import pyttsx3
except ModuleNotFoundError:
    pyttsx3 = None

try:
    import wikipedia
except ModuleNotFoundError:
    wikipedia = None

try:
    from ollama import chat
except ModuleNotFoundError:
    chat = None

if wikipedia is not None:
    wikipedia.set_lang("en")

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
MODEL = os.environ.get("JARVIS_MODEL", "llama3.2:3b")  # try qwen2.5:7b or llama3.1:8b for better tool use
MAX_STEPS = 5          # max model/tool rounds per request
MAX_HISTORY_TURNS = 8  # how many past user turns the model remembers
TASKS_FILE = Path("jarvis_tasks.json")

VOICE_ENABLED = True
VOICE_AVAILABLE = False
recognizer = None
microphone = None
sr = None
engine = None
tools_supported = True

SYSTEM_PROMPT = (
    "You are Jarvis, a smart, friendly personal assistant. "
    "Understand what the user means, not just their exact words. "
    "Use your tools when the user wants an action (tasks, opening sites, screenshots, "
    "time, weather, jokes, searching). Otherwise just talk naturally and think things through. "
    "Keep answers short because they are spoken aloud. Never claim to do something "
    "you have no tool for."
)


# ---------------------------------------------------------------------------
# Speech output
# ---------------------------------------------------------------------------
def init_engine() -> None:
    global engine
    if pyttsx3 is None:
        engine = None
        return
    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 150)
        engine.setProperty("volume", 0.9)

        voices = engine.getProperty("voices") or []
        for voice in voices:
            name = voice.name.lower()
            if "female" in name or "zira" in name:
                engine.setProperty("voice", voice.id)
                print(f"Voice set to: {voice.name}")
                return
        if voices:
            engine.setProperty("voice", voices[0].id)
            print(f"Voice set to: {voices[0].name}")
    except Exception as exc:
        print(f"Could not start the voice engine ({exc}). Continuing with text only.")
        engine = None


def speak(text: str) -> None:
    """Print the text, and say it aloud if a voice engine is available."""
    print(f"Jarvis: {text}")
    if engine is None:
        return
    try:
        engine.say(text)
        engine.runAndWait()
    except Exception as exc:
        print(f"Error speaking: {exc}")


def confirm(question: str) -> bool:
    """Ask a yes/no question by typing. Used before destructive actions."""
    speak(f"{question} Type yes or no.")
    try:
        return input("(yes/no): ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ---------------------------------------------------------------------------
# Speech input
# ---------------------------------------------------------------------------
def setup_voice() -> None:
    global VOICE_AVAILABLE, VOICE_ENABLED, recognizer, microphone, sr
    try:
        import speech_recognition as speech_recognition_module

        sr = speech_recognition_module
        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 0.8
        recognizer.dynamic_energy_threshold = True

        try:
            microphone = sr.Microphone()
            with microphone as source:
                recognizer.adjust_for_ambient_noise(source, duration=1)
        except Exception as exc:
            raise RuntimeError(f"Microphone/PyAudio problem: {exc}") from exc

        VOICE_AVAILABLE = True
        print("Voice recognition ready.")
    except Exception as exc:
        VOICE_AVAILABLE = False
        VOICE_ENABLED = False
        print(f"Voice recognition unavailable, using typed commands: {exc}")
        print("To enable it: python -m pip install SpeechRecognition pyaudio")


def set_voice_enabled(enabled: bool) -> None:
    global VOICE_ENABLED
    if enabled and not VOICE_AVAILABLE:
        speak("Voice recognition is unavailable on this system.")
        VOICE_ENABLED = False
        return
    VOICE_ENABLED = enabled
    speak("Voice recognition is now enabled." if enabled else "Voice recognition is now disabled.")


def take_command() -> str:
    """Listen by voice if possible, otherwise fall back to typing."""
    if VOICE_ENABLED and VOICE_AVAILABLE:
        try:
            with microphone as source:
                print("Listening...")
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
            print("Recognizing...")
            query = recognizer.recognize_google(audio, language="en-US")
            print(f"You: {query}")
            return query.strip().lower()
        except sr.WaitTimeoutError:
            print("(Heard nothing, type your command instead.)")
        except sr.UnknownValueError:
            print("(Could not understand, type your command instead.)")
        except sr.RequestError as exc:
            print(f"(Speech service unavailable: {exc}. Type your command instead.)")
        except Exception as exc:
            print(f"(Voice input error: {exc}. Type your command instead.)")

    try:
        return input("Command: ").strip().lower()
    except EOFError:
        return "quit"


def wish_me() -> None:
    hour = datetime.datetime.now().hour
    if hour < 12:
        speak("Good morning.")
    elif hour < 18:
        speak("Good afternoon.")
    else:
        speak("Good evening.")
    speak("I am Jarvis. How can I help you today?")


# ---------------------------------------------------------------------------
# Task storage
# ---------------------------------------------------------------------------
def load_tasks() -> list:
    try:
        if TASKS_FILE.exists():
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        print(f"Error loading tasks: {exc}")
    return []


def save_tasks(tasks: list) -> None:
    try:
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, indent=2)
    except Exception as exc:
        print(f"Error saving tasks: {exc}")


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value) -> bool:
    """Small models sometimes send booleans as strings like "false"."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


# ---------------------------------------------------------------------------
# Jokes
# ---------------------------------------------------------------------------
POKEMON_JOKES = [
    "Why did the Pokemon go to the doctor? Because it was feeling koffing!",
    "What do you call a Pokemon that's good at baseball? A Diglett!",
    "Why did Pikachu choose the yellow paint? Because he wanted to be Electrode!",
    "What's a Pokemon's favorite type of music? Rock and roll! Get it? Because of Geodude!",
    "Why did the Trainer go to school? To improve his Koffing skills!",
    "How do you know if there's a Dragonite in your refrigerator? Footprints in the butter!",
    "What do you call a Pokemon that's a detective? Inves-Pikachu!",
    "Why don't Slowpokes ever win at poker? Because they're always too slow to call!",
    "What's Mewtwo's favorite subject in school? Psycho-logy!",
    "Why did Snorlax go to the gym? To get shredded lettuce for his salad!",
    "What do you call a Wailord in the desert? Lost!",
    "Why is Jigglypuff always tired? Because it keeps singing people to sleep!",
    "What's the difference between a Grimer and a Muk? One letter!",
    "Why did the Growlithe cross the road? To get to the other Mankey side!",
    "What do you call a Pokemon that's a magician? Alakazam!",
]


# ---------------------------------------------------------------------------
# Tools the model can call. Docstrings are sent to the model, so keep them clear.
# ---------------------------------------------------------------------------
def tool_add_task(task_name: str) -> str:
    """Add a task to the user's to-do list.

    Args:
        task_name: A short description of the task.
    """
    tasks = load_tasks()
    new_id = max((t["id"] for t in tasks), default=0) + 1
    tasks.append({
        "id": new_id,
        "task": task_name,
        "completed": False,
        "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save_tasks(tasks)
    return f"Added task {new_id}: {task_name}"


def tool_list_tasks() -> str:
    """List all of the user's tasks with their ids and status."""
    tasks = load_tasks()
    if not tasks:
        return "There are no tasks."
    return "\n".join(
        f"{t['id']}: {t['task']} ({'done' if t['completed'] else 'pending'})" for t in tasks
    )


def tool_complete_task(task_id: int) -> str:
    """Mark a task as completed.

    Args:
        task_id: The numeric id of the task.
    """
    task_id = _to_int(task_id)
    if task_id is None:
        return "That is not a valid task id."
    tasks = load_tasks()
    for task in tasks:
        if task["id"] == task_id:
            task["completed"] = True
            save_tasks(tasks)
            return f"Task {task_id} marked complete."
    return f"Task {task_id} not found."


def tool_delete_task(task_id: int) -> str:
    """Delete a task. The user is asked to confirm first.

    Args:
        task_id: The numeric id of the task.
    """
    task_id = _to_int(task_id)
    if task_id is None:
        return "That is not a valid task id."
    tasks = load_tasks()
    target = next((t for t in tasks if t["id"] == task_id), None)
    if target is None:
        return f"Task {task_id} not found."
    if not confirm(f"Delete task {task_id}: {target['task']}?"):
        return "The user cancelled, nothing was deleted."
    save_tasks([t for t in tasks if t["id"] != task_id])
    return f"Task {task_id} deleted."


def tool_open_website(url: str) -> str:
    """Open a website in the browser.

    Args:
        url: Full address, for example https://www.youtube.com
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}"


def tool_google_search(query: str) -> str:
    """Search Google in the browser.

    Args:
        query: What to search for.
    """
    webbrowser.open(f"https://www.google.com/search?q={quote_plus(query)}")
    return f"Searched Google for {query}"


def tool_wikipedia(topic: str) -> str:
    """Get a short factual summary of a topic from Wikipedia.

    Args:
        topic: The subject to look up.
    """
    if wikipedia is None:
        return "Wikipedia is not installed."
    try:
        return wikipedia.summary(topic, sentences=2)
    except wikipedia.exceptions.DisambiguationError as exc:
        return f"'{topic}' is ambiguous. Options include: {', '.join(exc.options[:5])}"
    except Exception:
        return "Nothing found on Wikipedia."


def tool_weather(city: str) -> str:
    """Get the current weather for a city.

    Args:
        city: The city name, for example London.
    """
    url = f"https://wttr.in/{quote(city)}?format=3"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "curl"})
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.read().decode("utf-8").strip()
    except Exception as exc:
        return f"Could not get the weather: {exc}"


def tool_get_time() -> str:
    """Get the current local date and time."""
    return datetime.datetime.now().strftime("%A, %B %d %Y, %H:%M")


def open_file(path: str) -> None:
    """Open a file with the default app on any operating system."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)


def tool_screenshot() -> str:
    """Take a screenshot of the screen and open it."""
    if pyautogui is None:
        return "Screenshots are unavailable because pyautogui is not installed."
    try:
        filename = os.path.abspath("screenshot.png")
        pyautogui.screenshot().save(filename)
        open_file(filename)
        return f"Screenshot saved as {filename}."
    except Exception as exc:
        return f"Could not take a screenshot: {exc}"


def tool_joke(pokemon: bool = False) -> str:
    """Get a joke.

    Args:
        pokemon: True for a Pokemon joke, False for a normal programming joke.
    """
    if _to_bool(pokemon):
        return random.choice(POKEMON_JOKES)
    if pyjokes is None:
        return "Jokes are unavailable because pyjokes is not installed."
    return pyjokes.get_joke()


TOOLS = {
    tool.__name__: tool
    for tool in (
        tool_add_task,
        tool_list_tasks,
        tool_complete_task,
        tool_delete_task,
        tool_open_website,
        tool_google_search,
        tool_wikipedia,
        tool_weather,
        tool_get_time,
        tool_screenshot,
        tool_joke,
    )
}


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
history: list = []  # full conversation, including tool calls and results


def _role(message) -> str:
    return message["role"] if isinstance(message, dict) else message.role


def trim_history() -> None:
    """Keep only the last few user turns, cutting at a turn boundary so tool calls stay paired with results."""
    user_positions = [i for i, m in enumerate(history) if _role(m) == "user"]
    if len(user_positions) > MAX_HISTORY_TURNS:
        del history[: user_positions[-MAX_HISTORY_TURNS]]


def call_model(messages):
    global tools_supported
    if tools_supported:
        try:
            return chat(
                model=MODEL,
                messages=messages,
                tools=list(TOOLS.values()),
                keep_alive="30m",
            )
        except Exception as exc:
            if "does not support tools" not in str(exc).lower():
                raise
            tools_supported = False
            print(f"{MODEL} has no tool support, chatting without tools.")
    return chat(model=MODEL, messages=messages, keep_alive="30m")


def run_tool(call) -> str:
    tool = TOOLS.get(call.function.name)
    if tool is None:
        return f"Unknown tool: {call.function.name}"
    try:
        return str(tool(**(call.function.arguments or {})))
    except TypeError as exc:
        return f"Bad arguments for {call.function.name}: {exc}"
    except Exception as exc:
        return f"Tool error: {exc}"


def ask_ollama(query: str) -> None:
    """Let the model understand the request, call tools if needed, and answer."""
    if chat is None:
        speak("AI chat is unavailable. Install the Ollama Python package and make sure Ollama is running.")
        print("Install it with: python -m pip install ollama")
        return

    start = len(history)
    history.append({"role": "user", "content": query})
    trim_history()

    try:
        print("Jarvis is thinking... (the first reply can take a while)")
        finished = False
        used_tools = False
        answer = ""

        for _ in range(MAX_STEPS):
            response = call_model([{"role": "system", "content": SYSTEM_PROMPT}] + history)
            msg = response.message
            history.append(msg)

            if not msg.tool_calls:
                answer = (msg.content or "").strip()
                finished = True
                break

            used_tools = True
            for call in msg.tool_calls:
                history.append({
                    "role": "tool",
                    "tool_name": call.function.name,
                    "content": run_tool(call),
                })

        if not finished:
            answer = "Sorry, I could not finish that. Try asking in a simpler way."
            history.append({"role": "assistant", "content": answer})
        elif not answer:
            answer = "Done." if used_tools else "Sorry, I have nothing to say to that."

        speak(answer)

    except KeyboardInterrupt:
        del history[start:]
        speak("Okay, cancelled.")
    except Exception as exc:
        del history[start:]  # drop the half-finished turn so the next request starts clean
        print(f"Ollama chat error: {type(exc).__name__}: {exc}")
        speak("I could not get a response from Ollama. Check that it is running and the model is installed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    optional = {
        "pyautogui": pyautogui,
        "pyjokes": pyjokes,
        "pyttsx3": pyttsx3,
        "wikipedia": wikipedia,
        "ollama": chat,
    }
    missing = [name for name, module in optional.items() if module is None]
    if missing:
        print(f"Optional packages not installed: {', '.join(missing)}")
        print("Those features will be disabled. To get everything:")
        print("python -m pip install pyautogui pyjokes pyttsx3 wikipedia ollama SpeechRecognition pyaudio\n")

    init_engine()
    setup_voice()
    wish_me()

    while True:
        query = take_command()
        if not query:
            continue

        if query.strip(" .!") in ("quit", "exit", "stop", "goodbye"):
            speak("Goodbye.")
            break
        elif "disable voice recognition" in query or "voice recognition off" in query:
            set_voice_enabled(False)
        elif "enable voice recognition" in query or "voice recognition on" in query:
            set_voice_enabled(True)
        else:
            ask_ollama(query)


if __name__ == "__main__":
    main()
