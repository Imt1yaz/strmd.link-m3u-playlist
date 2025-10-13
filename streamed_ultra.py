# -*- coding: utf-8 -*-
"""
Created on Sun Oct 12 20:50:55 2025

@author: SKADOOSH
"""

"""
ULTRA-OPTIMIZED: Maximum Speed M3U8 Extraction
CUSTOM LOGIC:
- If admin exists → admin + alpha
- If alpha + echo + hotel exist → all three
- If alpha + echo exist → both
- If alpha + hotel exist → both  
- If hotel + echo exist → both
SOURCES: Admin, Alpha, Hotel, Echo ONLY
MAX: 3 embeds per source
Background chromme check - non headless
"""
import os
import subprocess
import requests
import json
import time
import re
import urllib3
from datetime import datetime
from urllib.parse import urlparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# API Configuration
API_BASE = "https://streamed.pk/api"

TARGET_SPORTS = {
    'football': 'Football',
    'motor-sports': 'Motor Sports',
    'motorsports': 'Motor Sports',
    'fight': 'Fight (UFC/Boxing)',
    'mma': 'Fight (UFC/Boxing)',
    'boxing': 'Fight (UFC/Boxing)',
    'cricket': 'Cricket'
}

# ⚡ Only test these 4 sources
PRIORITY_SOURCES = ['admin', 'alpha', 'hotel', 'echo']

# ⚡ Maximum embeds to test per source
MAX_EMBEDS_PER_SOURCE = 3

# ⚡⚡ ULTRA SPEED CONFIG
ULTRA_CONFIG = {
    'initial_wait': 4,
    'smart_page_load': True,
    'parallel_m3u8_test': True,
    'one_stream_per_match': True,  # keep as requested
    'max_workers': 3,
    'max_monitor_time': 25,
    'no_new_urls_timeout': 8,
    'test_interval': 2,
    'connection_pool_size': 10,
}

# Global URL cache
# Now keyed by (m3u8_url, origin) to avoid referer-origin false negatives/positives
TESTED_URLS_CACHE = set()           # failed attempts cache
KNOWN_WORKING_URLS = set()          # successful attempts cache
CACHE_LOCK = threading.Lock()

# Session pool
SESSION_POOL = requests.Session()
adapter = requests.adapters.HTTPAdapter(
    pool_connections=ULTRA_CONFIG['connection_pool_size'],
    pool_maxsize=ULTRA_CONFIG['connection_pool_size'],
    max_retries=1
)
SESSION_POOL.mount('http://', adapter)
SESSION_POOL.mount('https://', adapter)

# Proxy support (HTTP/HTTPS) via env PROXY_URL, e.g. "http://59.153.18.93:19201"
PROXY_URL = os.environ.get('PROXY_URL', '').strip()

def _ensure_scheme(u: str) -> str:
    if not u:
        return ''
    if not re.match(r'^[a-zA-Z]+://', u):
        return f'http://{u}'
    return u

if PROXY_URL:
    PROXY_URL = _ensure_scheme(PROXY_URL)
    # Apply to requests session
    SESSION_POOL.proxies.update({
        'http': PROXY_URL,
        'https': PROXY_URL
    })
    # Also set standard env vars so any library respecting them uses the proxy
    os.environ['HTTP_PROXY'] = PROXY_URL
    os.environ['HTTPS_PROXY'] = PROXY_URL


def setup_enhanced_driver(headless=False):
    """Setup driver with enhanced network capture and asset blocking"""
    options = uc.ChromeOptions()

    # Route Selenium traffic through the proxy too
    if PROXY_URL:
        options.add_argument(f'--proxy-server={PROXY_URL}')
        # Reduce IP leaks via WebRTC (keeps WebRTC on the proxied interface)
        options.add_argument('--force-webrtc-ip-handling-policy=disable_non_proxied_udp')
        options.add_argument('--enforce-webrtc-ip-permission-check')

    # Force UC to use Chrome for Testing if provided by setup-chrome
    chrome_bin = (
        os.environ.get("CHROME_PATH")
        or os.environ.get("GOOGLE_CHROME_BIN")
        or os.environ.get("GOOGLE_CHROME_SHIM")
    )

    # Derive major version from that binary (e.g., 141)
    version_main = None
    if chrome_bin:
        options.binary_location = chrome_bin
        try:
            out = subprocess.check_output(
                [chrome_bin, "--version"], stderr=subprocess.STDOUT
            ).decode().strip()
            # Matches both "Google Chrome for Testing 141.0.7390.76" and "Google Chrome 140.0.7339.207"
            m = re.search(r'(\d+)\.\d+\.\d+\.\d+', out)
            if m:
                version_main = int(m.group(1))
        except Exception:
            pass

        # Fallback to CHROME_VERSION env if available
        if not version_main:
            cv = os.environ.get("CHROME_VERSION", "")
            m2 = re.search(r'^(\d+)', cv)
            if m2:
                version_main = int(m2.group(1))

    
    if headless:
        options.add_argument('--headless=new')
    
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--mute-audio')
    options.add_argument('--disable-web-security')
    options.add_argument('--disable-extensions')
    # Run non-headless but in background/off-screen to avoid headless detection
    options.add_argument('--start-minimized')
    options.add_argument('--window-position=-10000,0')
    options.add_argument('--disable-background-timer-throttling')
    options.add_argument('--disable-backgrounding-occluded-windows')
    options.add_argument('--disable-renderer-backgrounding')
    options.add_argument('--autoplay-policy=no-user-gesture-required')
    
    options.set_capability('goog:loggingPrefs', {
        'performance': 'ALL',
        'browser': 'ALL'
    })
    
    try:
        driver = uc.Chrome(options=options, version_main=None)
        driver.execute_cdp_cmd('Network.enable', {})
        # Block non-essential assets for speed
        try:
            driver.execute_cdp_cmd('Network.setBlockedURLs', {
                'urls': ['*.png', '*.jpg', '*.jpeg', '*.gif', '*.webp', '*.svg', '*.css', '*.woff*', '*.ttf']
            })
        except Exception:
            pass
        
        driver.execute_cdp_cmd('Page.enable', {})
        driver.set_page_load_timeout(15)
        
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                delete Object.getPrototypeOf(navigator).webdriver;
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = {runtime: {}};
            '''
        })
        
        # Try to keep window hidden/off-screen even if created focused
        try:
            driver.minimize_window()
        except Exception:
            pass
        try:
            driver.set_window_position(-10000, 0)
        except Exception:
            pass
        
        return driver
        
    except Exception as e:
        print(f"❌ Driver setup failed: {e}")
        return None

def inject_mega_interceptor(driver):
    """Lightweight interceptor"""
    interceptor = """
    window.allM3U8 = new Set();
    
    (function() {
        const origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            try {
                if (url && (url.includes('.m3u8') || url.includes('/stream/'))) {
                    window.allM3U8.add(url);
                }
            } catch(e){}
            return origOpen.apply(this, arguments);
        };
    })();
    
    (function() {
        const origFetch = window.fetch;
        window.fetch = function(url, opts) {
            try {
                const urlStr = typeof url === 'string' ? url : (url && (url.url || url.href || ''));
                if (urlStr && (urlStr.includes('.m3u8') || urlStr.includes('/stream/'))) {
                    window.allM3U8.add(urlStr);
                }
            } catch(e){}
            return origFetch.apply(this, arguments);
        };
    })();
    
    setInterval(() => {
        try {
            document.querySelectorAll('video, audio').forEach(v => {
                if (v.src && v.src.includes('.m3u8')) window.allM3U8.add(v.src);
                if (v.currentSrc && v.currentSrc.includes('.m3u8')) window.allM3U8.add(v.currentSrc);
            });
        } catch(e){}
    }, 1000);
    
    setTimeout(() => {
        try {
            if (window.Hls && window.Hls.prototype) {
                const orig = window.Hls.prototype.loadSource;
                window.Hls.prototype.loadSource = function(url) {
                    try { window.allM3U8.add(url); } catch(e){}
                    return orig.call(this, url);
                };
            }
        } catch(e){}
    }, 1000);
    """
    
    try:
        driver.execute_script(interceptor)
        return True
    except:
        return False

def wait_for_page_ready(driver, timeout=10):
    """Smart page load detection"""
    if not ULTRA_CONFIG['smart_page_load']:
        time.sleep(ULTRA_CONFIG['initial_wait'])
        return
    
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script('return document.readyState') == 'complete'
        )
        time.sleep(2)
    except TimeoutException:
        time.sleep(ULTRA_CONFIG['initial_wait'])

def get_intercepted_urls(driver):
    """Get URLs from interceptor"""
    try:
        urls = driver.execute_script("return Array.from(window.allM3U8 || new Set());")
        return list(urls) if urls else []
    except:
        return []

def get_cdp_network_requests(driver):
    """Get network requests via CDP"""
    m3u8_urls = set()
    
    try:
        logs = driver.get_log('performance')
        
        for log in logs:
            try:
                message = json.loads(log['message'])['message']
                method = message.get('method', '')
                
                if method in ['Network.requestWillBeSent', 'Network.responseReceived']:
                    params = message.get('params', {})
                    url = params.get('request', {}).get('url') or params.get('response', {}).get('url')
                    
                    if url and ('.m3u8' in url.lower() or '/stream/' in url.lower()):
                        if not any(ad in url.lower() for ad in ['doubleclick', 'googlesyndication']):
                            m3u8_urls.add(url)
                            
            except:
                continue
    except:
        pass
    
    return list(m3u8_urls)

def smart_play_trigger(driver, main_window):
    """Fast play triggering"""
    try:
        played = driver.execute_script("""
            let success = false;
            document.querySelectorAll('video').forEach(v => {
                v.muted = true;
                try { v.play().then(() => success = true).catch(e => {}); } catch(e){}
            });
            return success;
        """)
        
        if played:
            time.sleep(1.5)
            return True
    except:
        pass
    
    for selector in ['.vjs-big-play-button', 'button[aria-label*="play" i]', 'video']:
        try:
            elem = driver.find_element(By.CSS_SELECTOR, selector)
            if elem.is_displayed():
                driver.execute_script("arguments[0].click();", elem)
                time.sleep(1)
                close_popups(driver, main_window)
                return True
        except:
            continue
    
    return False

def close_popups(driver, main_window):
    """Close popups"""
    try:
        for window in driver.window_handles:
            if window != main_window:
                driver.switch_to.window(window)
                driver.close()
        driver.switch_to.window(main_window)
    except:
        try:
            driver.switch_to.window(main_window)
        except:
            pass

def complete_m3u8_url(base_url):
    """Complete M3U8 URLs"""
    if base_url.endswith('.m3u8'):
        return [base_url]
    
    filenames = ['playlist.m3u8', 'index.m3u8', 'master.m3u8', 'mono.ts.m3u8', 'chunklist.m3u8']
    
    if not base_url.endswith('/'):
        base_url += '/'
    
    return [base_url + f for f in filenames]

def test_m3u8_url_fast(m3u8_url, referer):
    """Fast M3U8 URL testing with caching (keyed by URL + Origin)"""
    def get_origin(ref):
        p = urlparse(ref)
        return f"{p.scheme}://{p.netloc}"
    
    origin = get_origin(referer)
    key = (m3u8_url, origin)
    
    with CACHE_LOCK:
        if key in KNOWN_WORKING_URLS:
            return True, None, m3u8_url  # short-circuit: known working for this origin
        if key in TESTED_URLS_CACHE:
            return False, None, None      # already tried and failed for this origin
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Referer': referer,
        'Origin': origin,
    }
    
    try:
        response = SESSION_POOL.get(
            m3u8_url, 
            headers=headers, 
            timeout=8,
            verify=False, 
            allow_redirects=True
        )
        
        if response.status_code == 200:
            content = response.text
            if '#EXTM3U' in content or '#EXT-X-' in content:
                with CACHE_LOCK:
                    KNOWN_WORKING_URLS.add(key)
                return True, content, m3u8_url
        
        with CACHE_LOCK:
            TESTED_URLS_CACHE.add(key)
        
        return False, None, None
            
    except:
        with CACHE_LOCK:
            TESTED_URLS_CACHE.add(key)
        return False, None, None

def test_urls_parallel(urls, referer):
    """Test multiple M3U8 URLs in parallel"""
    if not urls:
        return False, None
    
    if not ULTRA_CONFIG['parallel_m3u8_test']:
        for base_url in urls:
            for url in complete_m3u8_url(base_url):
                is_valid, content, final_url = test_m3u8_url_fast(url, referer)
                if is_valid:
                    return True, final_url
        return False, None
    
    all_urls_to_test = []
    for base_url in urls:
        all_urls_to_test.extend(complete_m3u8_url(base_url))
    
    with ThreadPoolExecutor(max_workers=ULTRA_CONFIG['max_workers']) as executor:
        future_to_url = {
            executor.submit(test_m3u8_url_fast, url, referer): url 
            for url in all_urls_to_test
        }
        
        for future in as_completed(future_to_url):
            is_valid, content, final_url = future.result()
            if is_valid:
                for f in future_to_url:
                    try:
                        f.cancel()
                    except:
                        pass
                return True, final_url
    
    return False, None

def extract_m3u8_ultra_fast(driver, embed_url):
    """Ultra-fast M3U8 extraction"""
    all_m3u8 = set()
    
    try:
        driver.get(embed_url)
        main_window = driver.current_window_handle
        
        wait_for_page_ready(driver, timeout=8)
        
        close_popups(driver, main_window)
        inject_mega_interceptor(driver)
        
        # Trigger play BEFORE checking early URLs
        smart_play_trigger(driver, main_window)
        time.sleep(1)
        
        # Early check after playback attempt
        early_urls = get_intercepted_urls(driver)
        if early_urls:
            success, working_url = test_urls_parallel(early_urls, embed_url)
            if success:
                return [working_url], True
        
        start = time.time()
        prev_seen = set()
        last_new_url_time = time.time()
        
        while time.time() - start < ULTRA_CONFIG['max_monitor_time']:
            elapsed = int(time.time() - start)
            
            if elapsed % 8 == 0:
                close_popups(driver, main_window)
            
            intercepted = set(get_intercepted_urls(driver))
            cdp_urls = set(get_cdp_network_requests(driver))
            seen_now = intercepted | cdp_urls
            
            new_urls = seen_now - prev_seen
            if new_urls:
                last_new_url_time = time.time()
                success, working_url = test_urls_parallel(list(new_urls), embed_url)
                if success:
                    return [working_url], True
                prev_seen |= new_urls
            
            all_m3u8 |= seen_now
            
            if time.time() - last_new_url_time > ULTRA_CONFIG['no_new_urls_timeout']:
                break
            
            time.sleep(ULTRA_CONFIG['test_interval'])
        
        return list(all_m3u8), False
        
    except Exception:
        return list(all_m3u8), False

# NEW: quick wrapper for duplicate follow-up attempts (faster window)
def extract_m3u8_ultra_fast_quick(driver, embed_url):
    """Quick mode wrapper to speed up duplicate-follow-up attempts"""
    orig_max_monitor_time = ULTRA_CONFIG['max_monitor_time']
    orig_no_new_urls_timeout = ULTRA_CONFIG['no_new_urls_timeout']
    orig_test_interval = ULTRA_CONFIG['test_interval']
    try:
        # further shrink time windows for follow-up tries
        ULTRA_CONFIG['max_monitor_time'] = min(orig_max_monitor_time, 8)
        ULTRA_CONFIG['no_new_urls_timeout'] = min(orig_no_new_urls_timeout, 3)
        ULTRA_CONFIG['test_interval'] = 1
        return extract_m3u8_ultra_fast(driver, embed_url)
    finally:
        ULTRA_CONFIG['max_monitor_time'] = orig_max_monitor_time
        ULTRA_CONFIG['no_new_urls_timeout'] = orig_no_new_urls_timeout
        ULTRA_CONFIG['test_interval'] = orig_test_interval

def process_single_source(driver, source_name, embeds, match_title, sport, seen_working=None):
    """Process a single source - Maximum 5 embeds per source"""
    total_embeds = len(embeds)
    
    # ⚡ LIMIT: Only test first 3 embeds
    embeds_to_test = embeds[:MAX_EMBEDS_PER_SOURCE]
    
    if total_embeds > MAX_EMBEDS_PER_SOURCE:
        print(f"\n    📡 Processing source: '{source_name}' ({total_embeds} total, testing first {MAX_EMBEDS_PER_SOURCE})")
    else:
        print(f"\n    📡 Processing source: '{source_name}' ({total_embeds} embed(s))")
    
    for j, embed_info in enumerate(embeds_to_test, 1):
        embed_url = embed_info['url']
        print(f"\n      Embed {j}/{len(embeds_to_test)} from '{source_name}':")
        
        # Use quick mode for follow-up attempts (when chasing uniqueness after duplicates
        # or when we already have a working stream from another source)
        use_quick = (j > 1 and seen_working is not None and len(seen_working) > 0)
        
        embed_start = time.time()
        if use_quick:
            m3u8_urls, already_validated = extract_m3u8_ultra_fast_quick(driver, embed_url)
        else:
            m3u8_urls, already_validated = extract_m3u8_ultra_fast(driver, embed_url)
        embed_time = time.time() - embed_start
        
        if already_validated and m3u8_urls:
            working_url = m3u8_urls[0]
            
            # If duplicate of an already-found working stream, try next embed (up to MAX_EMBEDS_PER_SOURCE)
            base_url_candidate = working_url
            if seen_working is not None and base_url_candidate in seen_working:
                print(f"      ℹ️  Duplicate working stream detected (same M3U8). Trying next embed for '{source_name}'.")
                time.sleep(1)
                continue
            
            parsed = urlparse(embed_url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            url_with_headers = f"{working_url}|Referer={embed_url}&Origin={origin}"
            
            channel_name = clean_channel_name(match_title, sport, embed_info, source_name)
            
            result = {
                'name': channel_name,
                'url': url_with_headers,
                'sport': sport,
                'match': match_title,
                'source': source_name,
                'time': embed_time
            }
            
            print(f"\n      ✅ SUCCESS for '{source_name}'! (⚡ {embed_time:.1f}s)")
            print(f"         Channel: {channel_name}")
            print(f"         M3U8: {working_url[:100]}...")
            
            return result
        
        time.sleep(1)
    
    print(f"      ❌ No working M3U8 for '{source_name}'")
    return None

def fetch_popular_live_matches():
    print("\n🔍 Fetching live matches...")
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    try:
        response = SESSION_POOL.get(f"{API_BASE}/matches/live/popular", headers=headers, timeout=15, verify=False)
        if response.status_code == 200:
            matches = response.json()
            if isinstance(matches, list):
                print(f"✅ Found {len(matches)} matches")
                return matches
    except Exception as e:
        print(f"❌ Error: {e}")
    return []

def filter_matches_by_sport(matches):
    print("\n🎯 Filtering by target sports...")
    if not matches:
        return []
    
    filtered = []
    for match in matches:
        category = match.get('category', '').lower()
        if category in TARGET_SPORTS:
            filtered.append(match)
            sport_name = TARGET_SPORTS.get(category, category)
            print(f"  ✓ [{sport_name}] {match.get('title', 'Unknown')[:60]}")
    
    print(f"\n📊 Filtered: {len(filtered)}/{len(matches)} matches")
    return filtered

def fetch_stream_embeds_smart(match):
    """Fetch embeds - ONLY from admin/alpha/hotel/echo"""
    sources = match.get('sources', [])
    embeds_by_source = defaultdict(list)
    
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    
    for source in sources:
        raw_source_name = source.get('source')
        source_id = source.get('id')
        
        if not raw_source_name or not source_id:
            continue
        
        # normalize to lowercase for consistent logic
        source_name = raw_source_name.lower()
        
        # ⚡ FILTER: Only fetch from allowed sources
        if source_name not in PRIORITY_SOURCES:
            continue
        
        try:
            url = f"{API_BASE}/stream/{source_name}/{source_id}"
            response = SESSION_POOL.get(url, headers=headers, timeout=8, verify=False)
            
            if response.status_code == 200:
                streams = response.json()
                for stream in streams:
                    embed_url = stream.get('embedUrl')
                    if embed_url:
                        embeds_by_source[source_name].append({
                            'url': embed_url,
                            'language': stream.get('language', 'Unknown'),
                            'hd': stream.get('hd', False),
                            'streamNo': stream.get('streamNo', 0)
                        })
        except:
            continue
    
    return dict(embeds_by_source)

def determine_sources_to_process(embeds_by_source):
    """
    ⚡ CUSTOM LOGIC:
    1. If "admin" exists → process "admin" + "alpha" (if exists)
    2. If "alpha" + "echo" + "hotel" all exist → process all three
    3. If "alpha" + "echo" exist → process both
    4. If "alpha" + "hotel" exist → process both
    5. If "hotel" + "echo" exist → process both
    6. If only one exists → process that one
    """
    available_sources = list(embeds_by_source.keys())
    num_sources = len(available_sources)
    
    print(f"\n    📦 Available sources ({num_sources}): {', '.join(available_sources)}")
    
    if num_sources == 0:
        print(f"    ⚠️  No sources from [admin, alpha, hotel, echo] available")
        return []
    
    # Check which sources exist
    has_admin = 'admin' in available_sources
    has_alpha = 'alpha' in available_sources
    has_hotel = 'hotel' in available_sources
    has_echo = 'echo' in available_sources
    
    sources_to_process = []
    
    # RULE 1: If admin exists → admin + alpha (if exists)
    if has_admin:
        print(f"    ✅ RULE: 'admin' exists")
        sources_to_process.append('admin')
        if has_alpha:
            sources_to_process.append('alpha')
            print(f"    ✅ Will process: admin + alpha")
        else:
            print(f"    ✅ Will process: admin only")
    
    # RULE 2-5: No admin, check other combinations
    else:
        print(f"    ℹ️  'admin' not found, checking other combinations...")
        
        # RULE 2: alpha + echo + hotel all exist
        if has_alpha and has_echo and has_hotel:
            sources_to_process = ['alpha', 'echo', 'hotel']
            print(f"    ✅ RULE: alpha + echo + hotel exist")
            print(f"    ✅ Will process: alpha + echo + hotel")
        
        # RULE 3: alpha + echo exist
        elif has_alpha and has_echo:
            sources_to_process = ['alpha', 'echo']
            print(f"    ✅ RULE: alpha + echo exist")
            print(f"    ✅ Will process: alpha + echo")
        
        # RULE 4: alpha + hotel exist
        elif has_alpha and has_hotel:
            sources_to_process = ['alpha', 'hotel']
            print(f"    ✅ RULE: alpha + hotel exist")
            print(f"    ✅ Will process: alpha + hotel")
        
        # RULE 5: hotel + echo exist
        elif has_hotel and has_echo:
            sources_to_process = ['hotel', 'echo']
            print(f"    ✅ RULE: hotel + echo exist")
            print(f"    ✅ Will process: hotel + echo")
        
        # RULE 6: Only one exists
        elif has_alpha:
            sources_to_process = ['alpha']
            print(f"    ✅ RULE: Only alpha exists")
            print(f"    ✅ Will process: alpha only")
        elif has_hotel:
            sources_to_process = ['hotel']
            print(f"    ✅ RULE: Only hotel exists")
            print(f"    ✅ Will process: hotel only")
        elif has_echo:
            sources_to_process = ['echo']
            print(f"    ✅ RULE: Only echo exists")
            print(f"    ✅ Will process: echo only")
        else:
            # Fallback (shouldn't happen)
            sources_to_process = available_sources
            print(f"    ⚠️  Fallback: Using all available sources")
    
    return sources_to_process

def clean_channel_name(match_title, sport_name, stream_info=None, source_name=None):
    """Create channel name"""
    title = re.sub(r'(?i)(watch|live|stream|online|free|hd)', '', match_title)
    title = re.sub(r'\s+', ' ', title).strip()
    
    channel_name = f"[{sport_name}] {title}"
    
    if stream_info and stream_info.get('hd'):
        channel_name += " [HD]"
    
    if source_name:
        channel_name += f" ({source_name})"
    
    return channel_name[:120]

def main():
    print("=" * 80)
    print("⚡⚡ ULTRA-OPTIMIZED: Maximum Speed M3U8 Extraction")
    print("=" * 80)
    print("🎯 CUSTOM SOURCE LOGIC:")
    print("   1. If admin exists → admin + alpha")
    print("   2. If alpha + echo + hotel exist → all three")
    print("   3. If alpha + echo exist → both")
    print("   4. If alpha + hotel exist → both")
    print("   5. If hotel + echo exist → both")
    print(f"🎯 MAX EMBEDS PER SOURCE: {MAX_EMBEDS_PER_SOURCE}")
    print("=" * 80)
    print("Optimizations:")
    print(f"  ⚡ Sequential source processing (driver thread-safe)")
    print(f"  ⚡ Parallel M3U8 URL testing: {ULTRA_CONFIG['parallel_m3u8_test']}")
    print(f"  ⚡ Smart page load detection: {ULTRA_CONFIG['smart_page_load']}")
    print(f"  ⚡ Global URL caching: Enabled")
    print(f"  ⚡ Connection pooling: {ULTRA_CONFIG['connection_pool_size']} connections")
    print("=" * 80)
    
    matches = fetch_popular_live_matches()
    if not matches:
        print("❌ No matches found")
        return
    
    filtered = filter_matches_by_sport(matches)
    if not filtered:
        print("❌ No target matches")
        return
    
    print(f"\n🚀 Setting up for {len(filtered)} matches...")
    driver = setup_enhanced_driver(headless=False)
    
    if not driver:
        print("❌ Driver failed")
        return
    
    successful = []
    failed_matches = []
    
    try:
        overall_start = time.time()
        
        for i, match in enumerate(filtered, 1):
            match_start = time.time()
            
            title = match.get('title', 'Unknown')
            category = match.get('category', '').lower()
            sport = TARGET_SPORTS.get(category, category)
            
            print(f"\n{'='*80}")
            print(f"[{i}/{len(filtered)}] {title}")
            print(f"{'='*80}")
            
            embeds_by_source = fetch_stream_embeds_smart(match)
            
            if not embeds_by_source:
                print("    ⚠️  No embeds from allowed sources")
                failed_matches.append({'match': title, 'reason': 'No allowed sources'})
                continue
            
            sources_to_process = determine_sources_to_process(embeds_by_source)
            
            if not sources_to_process:
                print("    ⚠️  No valid sources to process")
                failed_matches.append({'match': title, 'reason': 'No sources'})
                continue
            
            # Track unique working base URLs per match to avoid duplicates across sources
            seen_working = set()  # base m3u8 (without headers)
            # Process sources sequentially
            sources_with_working = []
            
            for source_name in sources_to_process:
                embeds = embeds_by_source.get(source_name, [])
                
                result = process_single_source(driver, source_name, embeds, title, sport, seen_working)
                
                if result:
                    base_url = result['url'].split('|', 1)[0]
                    if base_url in seen_working:
                        print(f"      ℹ️  Duplicate working stream detected (same M3U8). Skipping add for '{source_name}'.")
                    else:
                        seen_working.add(base_url)
                        successful.append(result)
                        sources_with_working.append(source_name)
                    
                    # If one_stream_per_match mode, stop after first success
                    if ULTRA_CONFIG['one_stream_per_match']:
                        print(f"\n    ⏭️  One stream per match mode: Skipping remaining sources")
                        break
            
            match_time = time.time() - match_start
            
            if sources_with_working:
                print(f"\n    ✅ Match complete: {', '.join(sources_with_working)} (⚡ {match_time:.1f}s)")
            else:
                print(f"\n    ❌ No working streams (⚡ {match_time:.1f}s)")
                failed_matches.append({'match': title, 'reason': 'No working M3U8'})
            
            time.sleep(0.5)
    
    finally:
        print("\n🛑 Closing browser...")
        try:
            driver.quit()
        except Exception:
            pass
    
    overall_time = time.time() - overall_start
    
    # RESULTS
    print(f"\n{'='*80}")
    print(f"📊 FINAL RESULTS")
    print(f"{'='*80}")
    print(f"✅ Successful streams: {len(successful)}")
    print(f"❌ Failed matches: {len(failed_matches)}/{len(filtered)}")
    print(f"⚡ Total time: {overall_time/60:.1f} minutes ({overall_time:.0f}s)")
    if len(filtered) > 0:
        print(f"⚡ Avg time per match: {overall_time/len(filtered):.1f}s")
    
    # Always write a fresh live playlist (no timestamped file, no append)
    by_sport = defaultdict(list)
    for stream in successful:
        by_sport[stream['sport']].append(stream)

    filename = 'livematches.m3u'
    with open(filename, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        f.write(f'#PLAYLIST: Streamed.pk Ultra [Custom Logic] - {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
        f.write(f'#DESCRIPTION:{len(successful)} streams (fresh run)\n\n')

        for sport in sorted(by_sport.keys()):
            streams = by_sport[sport]
            f.write(f'# ====== {sport.upper()} ({len(streams)}) ======\n')
            for stream in streams:
                f.write(f'#EXTINF:-1 tvg-name="{stream["name"]}" group-title="{sport}",{stream["name"]}\n')
                f.write(f'{stream["url"]}\n')
            f.write('\n')

    print(f"\n✅ Playlist updated: {filename}")

    # Optional reporting (kept for console)
    if successful:
        avg_time = sum(s.get('time', 0) for s in successful) / len(successful)
        print(f"⚡ Avg extraction time: {avg_time:.1f}s per stream")

        print(f"\n📺 By sport:")
        for sport in sorted(by_sport.keys()):
            print(f"  {sport}: {len(by_sport[sport])}")

        by_source = defaultdict(int)
        for stream in successful:
            by_source[stream['source']] += 1

        print(f"\n📡 By source:")
        for source in sorted(by_source.keys()):
            print(f"  {source}: {by_source[source]}")
    else:
        print("ℹ️ No streams found this run; playlist cleared to fresh header only.")
    
    if failed_matches:
        print(f"\n❌ Failed matches:")
        for fail in failed_matches[:5]:
            print(f"  ✗ {fail['match'][:60]} - {fail['reason']}")
        if len(failed_matches) > 5:
            print(f"  ... and {len(failed_matches) - 5} more")
    
    print(f"\n{'='*80}")
    print("✅ Complete!")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
