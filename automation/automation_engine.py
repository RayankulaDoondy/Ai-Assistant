"""
Automation System - Desktop and Browser Control
"""
import logging
import subprocess
import platform
from typing import List, Optional

logger = logging.getLogger(__name__)


class DesktopAutomation:
    """Desktop automation using PyAutoGUI"""
    
    def __init__(self):
        try:
            import pyautogui
            self.pyautogui = pyautogui
            self.os_type = platform.system()
            logger.info(f"Desktop automation initialized (OS: {self.os_type})")
        except ImportError:
            logger.error("PyAutoGUI not installed")
            raise
    
    def open_application(self, app_name: str) -> bool:
        """
        Open an application
        
        Args:
            app_name: Application name (e.g., 'notepad', 'chrome', 'vs code')
            
        Returns:
            Success status
        """
        try:
            app_lower = app_name.lower()
            
            if self.os_type == "Windows":
                # Windows application names
                app_map = {
                    "notepad": "notepad.exe",
                    "calc": "calc.exe",
                    "calculator": "calc.exe",
                    "chrome": "chrome",
                    "firefox": "firefox",
                    "edge": "msedge",
                    "vs code": "code",
                    "vscode": "code",
                    "visual studio": "devenv",
                    "explorer": "explorer.exe",
                    "cmd": "cmd.exe",
                    "powershell": "powershell.exe",
                }
                
                cmd = app_map.get(app_lower, app_lower)
                subprocess.Popen(cmd)
                logger.info(f"Opened application: {app_name}")
                return True
            
            elif self.os_type == "Darwin":  # macOS
                subprocess.Popen(["open", "-a", app_name])
                logger.info(f"Opened application: {app_name}")
                return True
            
            else:  # Linux
                subprocess.Popen([app_name])
                logger.info(f"Opened application: {app_name}")
                return True
                
        except Exception as e:
            logger.error(f"Error opening application {app_name}: {str(e)}")
            return False
    
    def close_application(self, app_name: str) -> bool:
        """
        Close an application
        
        Args:
            app_name: Application name
            
        Returns:
            Success status
        """
        try:
            if self.os_type == "Windows":
                subprocess.run(["taskkill", "/IM", f"{app_name}.exe"], check=False)
            else:
                subprocess.run(["pkill", app_name], check=False)
            logger.info(f"Closed application: {app_name}")
            return True
        except Exception as e:
            logger.error(f"Error closing application: {str(e)}")
            return False
    
    def type_text(self, text: str, interval: float = 0.05):
        """
        Type text on keyboard
        
        Args:
            text: Text to type
            interval: Delay between characters
        """
        try:
            self.pyautogui.typewrite(text, interval=interval)
            logger.debug(f"Typed: {text[:50]}...")
        except Exception as e:
            logger.error(f"Error typing text: {str(e)}")
    
    def press_key(self, key: str):
        """
        Press a keyboard key
        
        Args:
            key: Key name (e.g., 'enter', 'escape', 'tab')
        """
        try:
            self.pyautogui.press(key)
            logger.debug(f"Pressed key: {key}")
        except Exception as e:
            logger.error(f"Error pressing key: {str(e)}")
    
    def click(self, x: int, y: int):
        """
        Click at coordinates
        
        Args:
            x: X coordinate
            y: Y coordinate
        """
        try:
            self.pyautogui.click(x, y)
            logger.debug(f"Clicked at ({x}, {y})")
        except Exception as e:
            logger.error(f"Error clicking: {str(e)}")
    
    def get_mouse_position(self) -> tuple:
        """Get current mouse position"""
        return self.pyautogui.position()
    
    def move_mouse(self, x: int, y: int, duration: float = 0.5):
        """Move mouse to coordinates"""
        try:
            self.pyautogui.moveTo(x, y, duration=duration)
            logger.debug(f"Moved mouse to ({x}, {y})")
        except Exception as e:
            logger.error(f"Error moving mouse: {str(e)}")


class BrowserAutomation:
    """Browser automation using Playwright"""
    
    def __init__(self):
        try:
            from playwright.sync_api import sync_playwright
            self.sync_playwright = sync_playwright
            self.browser = None
            self.context = None
            self.page = None
            logger.info("Browser automation initialized")
        except ImportError:
            logger.error("Playwright not installed")
            raise
    
    def open_browser(self, url: Optional[str] = None) -> bool:
        """
        Open browser and navigate to URL
        
        Args:
            url: URL to navigate to
            
        Returns:
            Success status
        """
        try:
            p = self.sync_playwright().start()
            self.browser = p.chromium.launch(headless=False)
            self.context = self.browser.new_context()
            self.page = self.context.new_page()
            
            if url:
                self.page.goto(url)
                logger.info(f"Opened browser and navigated to: {url}")
            else:
                logger.info("Opened browser")
            return True
        except Exception as e:
            logger.error(f"Error opening browser: {str(e)}")
            return False
    
    def close_browser(self) -> bool:
        """Close browser"""
        try:
            if self.page:
                self.page.close()
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            logger.info("Browser closed")
            return True
        except Exception as e:
            logger.error(f"Error closing browser: {str(e)}")
            return False
    
    def navigate(self, url: str) -> bool:
        """Navigate to URL"""
        try:
            if not self.page:
                self.open_browser()
            self.page.goto(url)
            logger.info(f"Navigated to: {url}")
            return True
        except Exception as e:
            logger.error(f"Error navigating: {str(e)}")
            return False
    
    def search(self, query: str, engine: str = "google") -> bool:
        """Search on search engine"""
        try:
            if engine.lower() == "google":
                url = f"https://www.google.com/search?q={query}"
            elif engine.lower() == "bing":
                url = f"https://www.bing.com/search?q={query}"
            else:
                url = f"https://www.google.com/search?q={query}"
            
            return self.navigate(url)
        except Exception as e:
            logger.error(f"Error searching: {str(e)}")
            return False
    
    def click_element(self, selector: str) -> bool:
        """Click element by CSS selector"""
        try:
            self.page.click(selector)
            logger.debug(f"Clicked element: {selector}")
            return True
        except Exception as e:
            logger.error(f"Error clicking element: {str(e)}")
            return False
    
    def fill_form(self, selector: str, text: str) -> bool:
        """Fill form field"""
        try:
            self.page.fill(selector, text)
            logger.debug(f"Filled form field: {selector}")
            return True
        except Exception as e:
            logger.error(f"Error filling form: {str(e)}")
            return False


# Global instances
_desktop = None
_browser = None


def get_desktop_automation() -> DesktopAutomation:
    """Get or create desktop automation instance"""
    global _desktop
    if _desktop is None:
        _desktop = DesktopAutomation()
    return _desktop


def get_browser_automation() -> BrowserAutomation:
    """Get or create browser automation instance"""
    global _browser
    if _browser is None:
        _browser = BrowserAutomation()
    return _browser
