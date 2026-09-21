# Day 01: Python Environment, Execution Flow & Memory References

---

## 1. Definition
Python एक **high-level, interpreted (byte-compiled), dynamically typed, garbage-collected** भाषा है। जब आप Python कोड चलाते हैं, तो यह सीधे मशीन कोड (0s और 1s) में कंपाइल नहीं होता; बल्कि CPython इंटरप्रेटर पहले इसे इंटरमीडिएट **Bytecode (`.pyc`)** में कंपाइल करता है और फिर **Python Virtual Machine (PVM)** उस बाइटकोड को एक-एक करके निष्पादित (evaluate) करता है।

---

## 2. Why? (Problem it Solves)
- **प्लेटफ़ॉर्म स्वतंत्रता (Write Once, Run Anywhere)**: बाइटकोड आर्किटेक्चर के कारण एक ही `.py` फ़ाइल Windows, Linux, या macOS पर बिना रीकंपाइल किए चल सकती है (बशर्ते सही OS-विशिष्ट इंटरप्रेटर मौजूद हो)।
- **आइसोलेटेड एनवायरनमेंट (Virtual Environments)**: प्रोडक्शन सर्वर पर अलग-अलग प्रोजेक्ट्स (जैसे एक Django 3.2 और दूसरा FastAPI 0.110) के डिपेंडेंसी कॉन्फ्लिक्ट्स को हल करने के लिए `venv` सिस्टम लाइब्रेरीज़ को प्रोजेक्ट-लेवल पर अलग (isolate) रखता है।

---

## 3. How? (Under the Hood / Working Principle)
CPython का निष्पादन चक्र (Execution Lifecycle):

```text
Source Code (.py)
       │
       ▼ [Parser & AST Generator]
Abstract Syntax Tree (AST)
       │
       ▼ [Bytecode Compiler]
Python Bytecode (.pyc / __pycache__)
       │
       ▼ [Evaluation Loop (ceval.c)]
Python Virtual Machine (PVM Stack) ───► System Calls / OS Hardware
```

1. **Parser**: कोड की सिंटैक्स जाँच करके टोकन्स को **Abstract Syntax Tree (AST)** में बदलता है।
2. **Bytecode Compiler**: AST को कॉम्पैक्ट इंस्ट्रक्शन्स (`LOAD_CONST`, `STORE_FAST`, `BINARY_OP`) में बदलता है।
3. **PVM Evaluation Loop**: `ceval.c` का एक विशाल `for(;;)` लूप बाइटकोड इंस्ट्रक्शन्स को C फंक्शन्स के ज़रिए CPU पर रन करता है।
4. **Memory Allocation**: Python में *सब कुछ एक ऑब्जेक्ट* (`PyObject`) है। हेडर में दो चीज़ें हमेशा होती हैं:
   - `ob_refcnt`: रेफरेंस काउंट (Garbage Collection के लिए)।
   - `ob_type`: टाइप पॉइंटर (जो बताता है कि ऑब्जेक्ट int, str या list है)।

---

## 4. Syntax & Basic Contract
```python
import sys
import dis

# Inspect memory identity and reference count
x = 42
memory_address = id(x)
ref_count = sys.getrefcount(x)

# Disassemble bytecode to see raw PVM instructions
dis.dis("x = 42 + 5")
```

---

## 5. Example 1: Conceptual Walkthrough
Python में वेरिएबल्स "बॉक्स" (कंटेनर) नहीं होते, बल्कि मेमोरी में मौजूद ऑब्जेक्ट्स के **लेबल (References / Pointers)** होते हैं:

```python
a = [1, 2, 3]
b = a  # 'b' नया लिस्ट नहीं बना रहा; यह उसी मेमोरी एड्रेस को पॉइंट कर रहा है!

b.append(4)
print(a)  # Output: [1, 2, 3, 4] -> 'a' भी बदल गया क्योंकि दोनों एक ही ऑब्जेक्ट हैं!
print(id(a) == id(b))  # True
```

---

## 6. Example 2: Edge Cases & Gotchas (Small Integer & String Interning)
CPython परफॉर्मेंस और मेमोरी बचाने के लिए छोटे इंटीजर्स (`-5` से `256`) और छोटे स्ट्रिंग्स को **Intern** (कैश) कर लेता है:

```python
x = 256
y = 256
print(x is y)  # True -> CPython singleton memory reuse

p = 1000
q = 1000
print(p is q)  # False (REPL me) -> अलग-अलग PyObject बने
print(p == q)  # True -> वैल्यू समान है
```
> **नियम**: मान (Value) तुलना के लिए हमेशा `==` इस्तेमाल करें; ऑब्जेक्ट पहचान (Identity) के लिए `is`।

---

## 7. Production-Grade Example
बैकएंड सिस्टम्स में स्टार्ट-अप पर एनवायरनमेंट इंटीग्रिटी चेक करना बेहद ज़रूरी होता है।
(विस्तृत कोड [example_02.py](example_02.py) में देखें)

```python
import sys

def verify_runtime_environment(min_version: tuple = (3, 11)) -> None:
    """Production startup assertion to prevent runtime incompatibilities."""
    if sys.version_info < min_version:
        raise RuntimeError(
            f"Fatal: Python {min_version[0]}.{min_version[1]}+ is required. "
            f"Detected: {sys.version}"
        )
    # Check if running inside an active virtualenv
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv:
        sys.stderr.write("WARNING: Application running directly in global environment!\n")
```

---

## 8. Common Mistakes & Antipatterns
- ❌ **Mistake**: ग्लोबल एनवायरनमेंट में `pip install <package>` चलाना।
  - ✅ **Correction**: हमेशा प्रोजेक्ट के रूट में `python -m venv .venv` बनाकर एक्टिवेट करें।
- ❌ **Mistake**: `is` और `==` को इंटरचेंजेबल समझना (`if status is "ACTIVE"`).
  - ✅ **Correction**: हमेशा `if status == "ACTIVE"` लिखें। `is` केवल Singletons (`None`, `True`, `False`) के लिए इस्तेमाल करें।
- ❌ **Mistake**: `.gitignore` में `.venv`, `__pycache__`, और `.env` को शामिल न करना।

---

## 9. Performance & Complexity
- **`id()` lookup**: $\mathcal{O}(1)$ (CPython में यह केवल `PyObject*` का मेमोरी एड्रेस लौटाता है)।
- **Type verification**: `isinstance(obj, Class)` vs `type(obj) is Class`:
  - `isinstance()` इनहेरिटेंस को भी सपोर्ट करता है और अत्यधिक ऑप्टिमाइज़्ड है ($\mathcal{O}(1)$)।
- **Bytecode Caching**: `.pyc` फ़ाइल्स से प्रोग्राम का रनटाइम स्पीड नहीं बढ़ता, लेकिन **स्टार्टअप टाइम (Startup Time)** काफी तेज़ हो जाता है क्योंकि पार्सिंग और कंपाइलेशन स्टेप स्किप हो जाता है।

---

## 10. Security Implications
- **Global Pollution**: ग्लोबल एनवायरनमेंट में थर्ड-पार्टी पैकेज इंस्टॉल करने से पूरे OS के Python टूल्स (जैसे Linux का `apt` या `yum`) क्रैश हो सकते हैं।
- **Secrets in Bytecode**: `.pyc` फ़ाइल्स कंपाइल होने के बावजूद प्लेनटेक्स्ट स्ट्रिंग्स और हार्डकोडेड पासवर्ड्स को स्टोर रखती हैं। इन्हें आसानी से रिवर्स-इंजीनियर किया जा सकता है। सीक्रेट्स को हमेशा `.env` या AWS Secrets Manager में रखें।

---

## 11. When to Use?
- **Virtual Environments (`venv`)**: हर एक बैकएंड प्रोजेक्ट, माइक्रो-सर्विस, या स्क्रिप्ट के लिए जो एक्सटर्नल लाइब्रेरीज पर निर्भर करती है।
- **`requirements.txt` vs Lockfiles**: डेवेलपमेंट के लिए `pip-tools` या `poetry.lock` इस्तेमाल करें, ताकि हर डेवलपर और CI/CD सर्वर पर सटीक वर्जन्स इंस्टॉल हों।

---

## 12. When NOT to Use?
- **Do NOT use `is` for value comparisons**: कभी भी स्ट्रिंग्स, नंबर्स, या टुपल्स के मान की तुलना के लिए `is` का उपयोग न करें; इंटरनिंग इंजन का व्यवहार CPython वर्जन्स और कंपाइलर फ्लैग्स पर निर्भर करता है।

---

## 13. Top Interview Questions
1. *CPython में Bytecode क्या है और यह मशीन कोड से कैसे भिन्न है?*
2. *Python में `is` और `==` के बीच मुख्य अंतर क्या है?*
3. *Virtualenv बैकग्राउंड में कैसे काम करता है (How does `venv` isolate packages)?*
4. *CPython में स्मॉल इंटीजर इंटरनिंग (Small Integer Caching) की रेंज क्या है और क्यों?*
5. *`sys.prefix` और `sys.base_prefix` में क्या अंतर होता है?*

---

## 14. Practice Problems
1. एक Python स्क्रिप्ट लिखें जो दिए गए फ़ंक्शन का बाइटकोड डिसअसेम्बल करे और उसमें इस्तेमाल होने वाले ऑपकोड्स (`opcodes`) की गिनती करे।
2. एक ऑटोमेटेड एनवायरनमेंट वैलिडेटर बनाएं जो रनटाइम पर चेक करे कि क्या वर्चुअल एनवायरनमेंट एक्टिव है और आवश्यक मिनिमम Python वर्जन मौजूद है।
3. मेमोरी एड्रेस एक्सपेरिमेंट्स: लिस्ट के म्यूटेशन और री-असाइनमेंट पर `id()` का व्यवहार टेस्ट करें।

---

## 15. 5-Minute Revision Notes
- Python कोड: Source (`.py`) ➔ Bytecode (`.pyc`) ➔ PVM (`ceval.c`).
- Python में हर वेरिएबल एक **पॉइंटर/रेफरेंस** है जो हीप मेमोरी में मौजूद `PyObject` को पॉइंट करता है।
- `PyObject` के हेडर में हमेशा `ob_refcnt` (रेफरेंस काउंट) और `ob_type` (डेटा टाइप पॉइंटर) होता है।
- `==` तुलना करता है वैल्यू की (`__eq__`); `is` तुलना करता है मेमोरी एड्रेस की (`id(a) == id(b)`).
- `venv` काम करता है `pyvenv.cfg` के ज़रिए जो `sys.prefix` को बदल देता है ताकि `site-packages` लोकल फ़ोल्डर से लोड हों।
