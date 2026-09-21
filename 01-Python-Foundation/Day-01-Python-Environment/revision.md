# Day 01: 5-Minute Rapid Revision Notes

सोने से पहले या इंटरव्यू से ठीक पहले इन बुलेट पॉइंट्स को 5 मिनट में दोहराएँ:

---

- ⚡ **Execution Pipeline:**
  `Source (.py)` ➔ `Lexing & Parsing` ➔ `Abstract Syntax Tree (AST)` ➔ `Bytecode (.pyc)` ➔ `PVM Loop (ceval.c)` ➔ `Machine Execution`.

- ⚡ **Bytecode Purpose:**
  प्लेटफ़ॉर्म इंडिपेंडेंस + फ़ास्ट स्टार्टअप। `.pyc` कोड को तेज़ नहीं चलाता, बल्कि पार्सिंग टाइम बचाता है।

- ⚡ **Memory Architecture:**
  - Python में वेरिएबल केवल **पॉइंटर्स / रेफरेंसेज** होते हैं।
  - `PyObject_HEAD` = `ob_refcnt` (8B) + `ob_type` (8B) = 16B हेडर ओवरहेड।
  - जब `ref_count == 0`, CPython मेमोरी को तुरंत फ्री कर देता है (Deterministic Garbage Collection)।

- ⚡ **Identity vs Equality:**
  - `a == b` ➔ मान समान हैं (`a.__eq__(b)` कॉल होता है)।
  - `a is b` ➔ मेमोरी एड्रेस समान है (`id(a) == id(b)`).
  - केवल `None`, `True`, `False` के साथ `is` इस्तेमाल करें।

- ⚡ **Interning Optimization:**
  - Small Integers: `[-5, 256]` CPython स्टार्टअप पर ही सिंगलटन बन जाते हैं।
  - String Interning: वैलिड आइडेंटिफायर दिखने वाले छोटे स्ट्रिंग्स को CPython कैश करता है।

- ⚡ **Virtual Environment Rules:**
  - `sys.prefix != sys.base_prefix` ➔ Virtualenv एक्टिव है।
  - `pyvenv.cfg` सिस्टम Python का होम पाथ स्टोर करती है।
  - प्रोडक्शन डॉकर कंटेनर्स में `PYTHONDONTWRITEBYTECODE=1` सेट करना बेस्ट प्रैक्टिस है।

- ⚡ **Git Hygiene:**
  - हमेशा `.env`, `venv/`, `__pycache__/`, `*.pyc` को `.gitignore` में रखें।
