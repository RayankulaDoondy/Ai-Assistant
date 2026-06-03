"""
CLI Interface - Command Line Interface for Jarvis
For local testing and voice interaction
"""
import logging
import re
import sys
from typing import Optional
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from brain import get_llm_engine, get_context_manager, get_reasoning
from memory import get_memory_store, ConversationMemory
from voice import get_stt, get_tts, get_wakeword_detector
from automation import get_desktop_automation, get_browser_automation
from config import settings

# Setup
logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)
console = Console()


class JarvisCLI:
    """Command-line interface for Jarvis"""
    
    def __init__(self):
        """Initialize CLI components"""
        logger.info("Initializing Jarvis CLI...")
        
        self.llm_engine = get_llm_engine()
        self.context_manager = get_context_manager()
        self.reasoning_engine = get_reasoning()
        self.memory_store = get_memory_store()
        self.conversation_memory = ConversationMemory(self.memory_store)
        
        self.stt = get_stt(
            settings.SPEECH_TO_TEXT_MODEL,
            settings.STT_LANGUAGE,
            settings.AUDIO_INPUT_DEVICE,
        )
        self.tts = get_tts(settings.TTS_VOICE)
        self.wakeword = get_wakeword_detector(settings.WAKE_WORD)
        self.voice_enabled = settings.VOICE_OUTPUT.lower() == "enabled" and self.tts.available
        self.voice_available = self.tts.available
        self.voice_input_available = getattr(self.stt, "microphone_available", False)
        
        self.desktop = get_desktop_automation()
        self.browser = get_browser_automation()
        
        self.running = False
        logger.info("✓ Jarvis CLI initialized")
    
    def display_welcome(self):
        """Display welcome message"""
        if self.voice_available:
            voice_status = "enabled" if self.voice_enabled else "disabled"
            voice_note = f"Voice conversation mode is {voice_status}."
        else:
            voice_note = "Voice backend not available. Install pyttsx3 for speech output."
        welcome_text = f"""
        # 🤖 Welcome to Jarvis
        
        Your Personal AI Assistant (v{settings.APP_VERSION})
        
        **{voice_note}**
        
        **Commands:**
        - Type your question or command
        - `help` - Show help
        - `memory` - View memory
        - `voice` - Toggle voice mode
        - `voice on` / `voice off` - Enable or disable voice
        - `listen` - Capture speech once from microphone
        - `talk` - Enter two-way voice conversation mode
        - `mics` - List available microphone devices
        - `mic test` - Check whether Jarvis hears microphone audio
        - Say one of `{settings.VOICE_STOP_KEYWORDS}` to end voice conversation
        - Set `AUTO_START_VOICE_CONVERSATION=false` to keep text mode on launch
        - `clear` - Clear context
        - `exit` - Exit Jarvis
        """
        
        console.print(Panel(Markdown(welcome_text), title="Jarvis", border_style="cyan"))
    
    def process_command(self, user_input: str) -> Optional[str]:
        """
        Process user command
        
        Args:
            user_input: User input string
            
        Returns:
            Response or None
        """
        cmd = user_input.lower().strip()
        
        # Built-in commands
        if cmd == "help":
            self.show_help()
            return None
        
        elif cmd == "clear":
            self.context_manager.clear_context()
            console.print("[yellow]Context cleared[/yellow]")
            return None
        
        elif cmd == "memory":
            self.show_memory()
            return None
        
        elif cmd == "status":
            self.show_status()
            return None
        
        elif cmd.startswith("voice"):
            parts = cmd.split()
            if not self.voice_available:
                console.print("[red]TTS backend not available. Install pyttsx3 to use voice output.[/red]")
                return None
            if len(parts) == 1:
                self.voice_enabled = not self.voice_enabled
            elif parts[1] in {"on", "enable", "enabled"}:
                self.voice_enabled = True
            elif parts[1] in {"off", "disable", "disabled"}:
                self.voice_enabled = False
            status = "enabled" if self.voice_enabled else "disabled"
            console.print(f"[cyan]Voice output {status}[/cyan]")
            if self.voice_enabled:
                try:
                    self.tts.speak("Voice output enabled.")
                except Exception as e:
                    logger.error(f"TTS validation failed: {str(e)}")
            return None
        elif cmd == "listen":
            self.listen_once()
            return None
        elif cmd in {"talk", "voice chat", "voice conversation"}:
            self.start_voice_conversation()
            return None
        elif cmd in {"mics", "microphones", "list mics"}:
            self.show_microphones()
            return None
        elif cmd in {"mic test", "test mic", "microphone test"}:
            self.test_microphone()
            return None
        
        elif cmd == "models":
            models = self.llm_engine.get_available_models()
            console.print(f"[cyan]Available models: {', '.join(models)}[/cyan]")
            return None
        
        elif cmd == "exit" or cmd == "quit":
            self.running = False
            console.print("[yellow]Goodbye![/yellow]")
            return None
        
        # Regular chat
        else:
            return self.process_chat(user_input)
    
    def process_chat(self, user_input: str) -> str:
        """
        Process chat message
        
        Args:
            user_input: User message
            
        Returns:
            AI response
        """
        try:
            # Analyze intent
            intent_analysis = self.reasoning_engine.analyze_intent(user_input)
            intent = intent_analysis["primary_intent"]
            
            console.print(f"\n[blue]Intent: {intent}[/blue]")
            
            # Get context
            context = self.conversation_memory.get_context(user_input, limit=3)
            if self.voice_enabled:
                voice_instruction = (
                    f"Voice conversation mode is active. Reply naturally in "
                    f"{settings.VOICE_RESPONSE_MAX_WORDS} words or fewer unless the user asks for detail."
                )
                context = f"{context}\n\n{voice_instruction}" if context else voice_instruction
            
            # Generate response
            response = self.llm_engine.generate(user_input, context)
            
            # Store in memory
            self.conversation_memory.add_exchange(user_input, response)
            
            # Handle specific intents
            if intent == "open_app":
                # Extract app name and open
                app_name = user_input.split("open", 1)[-1].strip()
                self.desktop.open_application(app_name)
                response += f"\n[green]Opened {app_name}[/green]"
            
            elif intent == "search":
                # Extract query and search
                query = user_input.split("search", 1)[-1].strip()
                self.browser.search(query)
                response += f"\n[green]Searched for: {query}[/green]"
            
            if self.voice_enabled:
                try:
                    clean_response = self._strip_rich_markup(response)
                    self.tts.speak(clean_response)
                    console.print("[magenta]Jarvis is speaking...[/magenta]")
                except Exception as e:
                    logger.error(f"TTS playback failed: {str(e)}")
            
            return response
        
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error(f"Chat error: {str(e)}")
            return f"[red]Error: {str(e)}[/red]"
    
    def _strip_rich_markup(self, text: str) -> str:
        """
        Remove simple rich markup tags from text before speaking.

        Args:
            text: Text to clean

        Returns:
            Cleaned text without rich markup
        """
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[[^\]]+\]", "", text)
        text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", "", text)
        text = re.sub(r"[*_`#>{}\[\]()]|```[\s\S]*?```", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def listen_once(self):
        """Record one microphone input and use it as the user prompt."""
        if not self.voice_input_available:
            console.print("[red]Microphone input not available. Install sounddevice and soundfile.[/red]")
            return

        console.print(f"[yellow]Listening for {settings.VOICE_LISTEN_DURATION} seconds... Speak now.[/yellow]")
        transcript = self.stt.listen(
            duration=settings.VOICE_LISTEN_DURATION,
            sample_rate=settings.AUDIO_SAMPLE_RATE,
            min_peak_level=settings.VOICE_MIN_PEAK_LEVEL,
        )
        if not transcript:
            console.print("[red]No speech detected. Try again.[/red]")
            return

        console.print(f"[green]You said:[/green] {transcript}")
        response = self.process_chat(transcript)
        console.print(f"\n[green]Jarvis:[/green] {response}\n")

    def start_voice_conversation(self):
        """Start a continuous voice conversation loop."""
        if not self.voice_input_available or not self.voice_available:
            console.print("[red]Voice conversation requires both microphone and TTS support.[/red]")
            return

        self.voice_enabled = True
        try:
            self.tts.speak("Entering voice conversation mode. Say stop to end the conversation.")
        except Exception as e:
            logger.error(f"TTS validation failed: {str(e)}")

        stop_keywords = self.get_voice_stop_keywords()
        empty_attempts = 0
        try:
            while True:
                console.print(f"[yellow]Listening for {settings.VOICE_LISTEN_DURATION} seconds... Speak now.[/yellow]")
                transcript = self.stt.listen(
                    duration=settings.VOICE_LISTEN_DURATION,
                    sample_rate=settings.AUDIO_SAMPLE_RATE,
                    min_peak_level=settings.VOICE_MIN_PEAK_LEVEL,
                )
                if not transcript:
                    empty_attempts += 1
                    console.print("[red]No speech detected. Try again.[/red]")
                    if empty_attempts >= settings.VOICE_MAX_EMPTY_ATTEMPTS:
                        console.print(
                            "[yellow]Returning to text mode. Type `mics` or `mic test` to troubleshoot microphone input.[/yellow]"
                        )
                        break
                    continue
                empty_attempts = 0

                cleaned = transcript.strip().lower()
                if cleaned in stop_keywords:
                    try:
                        self.tts.speak("Voice conversation ended. Goodbye.")
                    except Exception:
                        pass
                    console.print("[yellow]Voice conversation ended.[/yellow]")
                    break

                console.print(f"[green]You said:[/green] {transcript}")
                response = self.process_chat(transcript)
                console.print(f"\n[green]Jarvis:[/green] {response}\n")
        except KeyboardInterrupt:
            console.print("\n[yellow]Voice conversation interrupted. Returning to text mode.[/yellow]")

    def get_voice_stop_keywords(self) -> set[str]:
        """Get normalized phrases that end voice conversation mode."""
        return {
            keyword.strip().lower()
            for keyword in settings.VOICE_STOP_KEYWORDS.split(",")
            if keyword.strip()
        }

    def show_microphones(self):
        """Show available microphone input devices."""
        if not self.voice_input_available:
            console.print("[red]Microphone input not available. Install sounddevice and soundfile.[/red]")
            return

        default_device = self.stt.get_default_input_device()
        configured_device = settings.AUDIO_INPUT_DEVICE or "default"
        console.print(f"[cyan]Configured input device:[/cyan] {configured_device}")
        if default_device:
            console.print(
                f"[cyan]System default input:[/cyan] "
                f"{default_device['id']} - {default_device['name']}"
            )

        devices = self.stt.list_input_devices()
        if not devices:
            console.print("[yellow]No microphone input devices found.[/yellow]")
            return

        console.print("\n[cyan]Available microphone devices:[/cyan]")
        for device in devices:
            console.print(
                f"{device['id']}: {device['name']} "
                f"({device['channels']} channels, {device['sample_rate']} Hz)"
            )

    def test_microphone(self):
        """Record briefly and report whether audio is reaching Jarvis."""
        if not self.voice_input_available:
            console.print("[red]Microphone input not available. Install sounddevice and soundfile.[/red]")
            return

        console.print("[yellow]Testing microphone for 3 seconds. Speak now.[/yellow]")
        result = self.stt.test_microphone(duration=3, sample_rate=settings.AUDIO_SAMPLE_RATE)
        if not result:
            console.print("[red]Microphone test failed. Try `mics` and set AUDIO_INPUT_DEVICE.[/red]")
            return

        console.print(
            f"[cyan]Mic level:[/cyan] peak={result['peak']:.4f}, "
            f"avg={result['average']:.4f}, sample_rate={result['sample_rate']}"
        )
        if result["has_signal"]:
            console.print("[green]Jarvis is receiving microphone audio.[/green]")
        else:
            console.print(
                "[red]Jarvis recorded near-silence. Check Windows microphone permissions, "
                "input volume, or choose another device with `AUDIO_INPUT_DEVICE`.[/red]"
            )

    def show_help(self):
        """Show help information"""
        help_text = """
        # Jarvis Commands
        
        **Basic:**
        - Ask any question in natural language
        - `help` - Show this help
        - `clear` - Clear context
        - `memory` - Show memory
        - `status` - Show system status
        - `models` - List available models
        - `exit` - Exit Jarvis

        **Voice:**
        - `listen` - Capture one spoken prompt
        - `talk` - Start continuous voice conversation
        - `mics` - List microphone devices
        - `mic test` - Check microphone audio level
        
        **Automation:**
        - "Open VS Code" - Open applications
        - "Search for..." - Search the web
        - "Close Chrome" - Close applications
        
        **Memory:**
        - "Remember that..." - Store information
        - "What do you know about..." - Retrieve information
        """
        console.print(Panel(Markdown(help_text), title="Help", border_style="cyan"))
    
    def show_memory(self):
        """Show memory statistics"""
        try:
            all_memories = self.memory_store.get_all_memories(limit=10)
            
            if not all_memories:
                console.print("[yellow]No memories stored yet[/yellow]")
                return
            
            console.print(f"\n[cyan]Total memories: {len(self.memory_store.get_all_memories())}[/cyan]")
            console.print("\n[cyan]Recent memories:[/cyan]")
            
            for i, memory in enumerate(all_memories, 1):
                content_preview = memory["content"][:100]
                mem_type = memory["metadata"].get("type", "unknown")
                console.print(f"{i}. [{mem_type}] {content_preview}...")
        except Exception as e:
            logger.error(f"Error showing memory: {str(e)}")
            console.print(f"[red]Error: {str(e)}[/red]")
    
    def show_status(self):
        """Show system status"""
        try:
            llm_ok = self.llm_engine.check_connection()
            models = self.llm_engine.get_available_models()
            memory_count = len(self.memory_store.get_all_memories())
            
            status_text = f"""
            **Jarvis Status**
            
            - LLM: {'✓ Connected' if llm_ok else '✗ Disconnected'}
            - Current Model: {settings.LLM_MODEL}
            - Available Models: {len(models)}
            - Memories Stored: {memory_count}
            - Memory Type: ChromaDB
            - Voice Output: {'✓ Enabled' if self.voice_enabled else '✗ Disabled'}
            - Microphone Input: {'✓ Available' if self.voice_input_available else '✗ Unavailable'}
            - Auto Voice Conversation: {'✓ Enabled' if settings.AUTO_START_VOICE_CONVERSATION else '✗ Disabled'}
            """
            
            console.print(Panel(Markdown(status_text), title="System Status", border_style="green"))
        except Exception as e:
            logger.error(f"Error showing status: {str(e)}")
            console.print(f"[red]Error: {str(e)}[/red]")
    
    def run(self):
        """Main CLI loop"""
        self.display_welcome()
        
        # Check LLM connection
        if not self.llm_engine.check_connection():
            console.print("[red]Warning: Cannot connect to Ollama![/red]")
            console.print("[yellow]Make sure Ollama is running: ollama serve[/yellow]")
            return
        
        self.running = True

        if self.should_auto_start_voice_conversation():
            console.print("[cyan]Auto-starting voice conversation mode...[/cyan]")
            self.start_voice_conversation()
        elif settings.AUTO_START_VOICE_CONVERSATION:
            console.print(
                "[yellow]Voice conversation auto-start is enabled, but microphone or TTS support is unavailable.[/yellow]"
            )
        
        while self.running:
            try:
                # Get user input
                user_input = console.input("\n[cyan]You:[/cyan] ").strip()
                
                if not user_input:
                    continue
                
                # Process command or chat
                response = self.process_command(user_input)
                
                if response:
                    console.print(f"\n[green]Jarvis:[/green] {response}\n")
            
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted[/yellow]")
                self.running = False
            except Exception as e:
                logger.error(f"Error in CLI loop: {str(e)}")
                console.print(f"[red]Error: {str(e)}[/red]")

    def should_auto_start_voice_conversation(self) -> bool:
        """Return True when startup settings and audio backends allow voice chat."""
        return (
            settings.AUTO_START_VOICE_CONVERSATION
            and self.voice_enabled
            and self.voice_input_available
            and self.voice_available
        )


def main():
    """Main entry point"""
    try:
        cli = JarvisCLI()
        cli.run()
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        console.print(f"[red]Fatal error: {str(e)}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
