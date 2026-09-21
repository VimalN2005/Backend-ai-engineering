# Day 01: Common Mistakes & Antipatterns (Python Environment & Memory)

यहाँ वो 7 सबसे घातक गलतियाँ दी गई हैं जो शुरुआती और यहाँ तक कि इंटरमीडिएट डेवलपर्स भी Python Environment और Memory मैनेजमेंट में करते हैं:

---

### 1. Using `is` Instead of `==` for Value Comparison
- ❌ **The Mistake:**
  ```python
  status = input("Enter status: ")  # User types "SUCCESS"
  if status is "SUCCESS":
      process_order()
  ```
- ⚠️ **The Problem:** `is` ऑपरेटर मेमोरी एड्रेस (`id(a) == id(b)`) की तुलना करता है, जबकि `==` ऑब्जेक्ट के मान (`__eq__`) की। CPython स्ट्रिंग्स को हमेशा इंटर्न (इंटरनल मेमोरी में री-यूज़) नहीं करता—खासकर डायनामिक इनपुट या स्पेस वाले स्ट्रिंग्स को। यह कोड कभी चलेगा और कभी साइलेंटली फ़ेल हो जाएगा!
- ✅ **The Correction:**
  ```python
  if status == "SUCCESS":
      process_order()
  ```
  > **Rule of Thumb:** `is` का उपयोग केवल Python singletons (`None`, `True`, `False`) के लिए करें: `if result is None:`.

---

### 2. Modifying a Mutable Object Through an Alias
- ❌ **The Mistake:**
  ```python
  default_config = {"timeout": 30, "retries": 3}
  user_config = default_config
  user_config["timeout"] = 60

  # default_config भी 60 हो गया!
  print(default_config["timeout"])  # 60
  ```
- ⚠️ **The Problem:** `user_config = default_config` कोई नई कॉपी नहीं बनाता; यह केवल उसी डिक्शनरी के मेमोरी एड्रेस का दूसरा रेफरेंस बनाता है।
- ✅ **The Correction:**
  ```python
  import copy

  # Shallow copy for flat dictionaries:
  user_config = default_config.copy()
  # Deep copy if config contains nested dictionaries/lists:
  user_config = copy.deepcopy(default_config)
  ```

---

### 3. Installing Dependencies Globally on Production / Host Machines
- ❌ **The Mistake:**
  ```bash
  pip install django fastapi celery
  ```
- ⚠️ **The Problem:** यह OS-लेवल `site-packages` को दूषित करता है। Linux/Debian जैसे सिस्टम्स पर OS के आंतरिक टूल्स (जैसे `apt-get`, `cloud-init`) Python 3 पर चलते हैं। ग्लोबल पैकेज अपग्रेड करने से OS सिस्टम टूल्स क्रैश हो सकते हैं।
- ✅ **The Correction:**
  ```bash
  python -m venv .venv
  # Windows:
  .venv\Scripts\activate
  # Linux / macOS:
  source .venv/bin/activate
  pip install -r requirements.txt
  ```

---

### 4. Committing `.env` and `__pycache__` to Git
- ❌ **The Mistake:**
  `git add .` चलाकर डेटाबेस पासवर्ड्स वाली `.env` फ़ाइल और कंपाइल्ड `.pyc` फ़ाइल्स GitHub पर पुश कर देना।
- ⚠️ **The Problem:**
  1. API Keys और Database Credentials लीक हो जाते हैं।
  2. `.pyc` फाइल्स दूसरे डेवलपर्स के OS पर बाइटकोड मिसमैच और मर्ज़ कॉन्फ्लिक्ट पैदा करती हैं।
- ✅ **The Correction:**
  प्रोजेक्ट के रूट में `.gitignore` बनाएं और उसमें शामिल करें:
  ```gitignore
  .env
  .venv/
  __pycache__/
  *.py[cod]
  ```

---

### 5. Blindly Relying on `sys.getsizeof()` for Container Memory
- ❌ **The Mistake:**
  ```python
  import sys
  large_list = ["a" * 1000000, "b" * 1000000]
  print(sys.getsizeof(large_list))  # Only ~72 bytes!
  ```
- ⚠️ **The Problem:** `sys.getsizeof()` केवल कंटेनर (पॉइंटर्स की लिस्ट) का साइज़ बताता है, उन स्ट्रिंग्स का नहीं जिन्हें वे पॉइंटर्स रेफरेंस कर रहे हैं!
- ✅ **The Correction:** डीप मेमोरी इंस्पेक्शन के लिए `pympler.asizeof` या `tracemalloc` का उपयोग करें।

---

### 6. Mutable Default Arguments in Functions
- ❌ **The Mistake:**
  ```python
  def append_to_cache(item, cache=[]):
      cache.append(item)
      return cache

  print(append_to_cache(1))  # [1]
  print(append_to_cache(2))  # [1, 2] -> Previous item persisted!
  ```
- ⚠️ **The Problem:** डिफॉल्ट आर्गुमेंट्स फ़ंक्शन के **डेफिनिशन टाइम** पर ही केवल एक बार हीप में इवैल्यूएट होते हैं, हर कॉल पर नहीं।
- ✅ **The Correction:**
  ```python
  def append_to_cache(item, cache=None):
      if cache is None:
          cache = []
      cache.append(item)
      return cache
  ```

---

### 7. Hardcoding Absolute System Paths
- ❌ **The Mistake:**
  ```python
  config_path = "C:\\Users\\admin\\project\\config.json"  # Fails on Linux servers
  ```
- ⚠️ **The Problem:** बैकएंड कोड लोकल Windows पर चलेगा लेकिन Linux Docker कंटेनर या AWS EC2 पर क्रैश हो जाएगा।
- ✅ **The Correction:** हमेशा `pathlib.Path` का उपयोग करें:
  ```python
  from pathlib import Path
  BASE_DIR = Path(__file__).resolve().parent.parent
  config_path = BASE_DIR / "config.json"
  ```
