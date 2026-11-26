from llama_cpp import Llama
import json
import gc
import os


MODEL_PATH = "model/qwen2.5-1.5b-instruct-q4_0.gguf"


def parse_recipe_with_llm(text):
    llm = Llama(
        model_path=MODEL_PATH,
        n_ctx=4096,
        n_threads=os.cpu_count(),
        n_gpu_layers=0,  # CPU only
        verbose=False
    )

    prompt = f"""
You are a parser.

Extract ONE cooking recipe from the text.
Return a valid JSON object with :
one big dictionary with the following elements: nom, temps_cuisson, temps, pour, type_plat, type_cuisson, ingredients (list of dictionaries with nom and quantité of each ingredients), etapes (list of strings).   

{{
    "nom": "test",
    "temps_cuisson": 45,
    "temps": "45",
    "pour": 4,
    "type_plat": "Plat",
    "type_cuisson": "Four",
    "ingredients": [
        {{
            "nom": "FARINE",
            "quantité": "500GR"
        }},
        {{
            "nom": "oeuf",
            "quantité": "3"
        }},
        {{
            "nom": "sachet de levure",
            "quantité": "1"
        }}
    ],
    "etapes": [
        "etape1",
        "etape2",
        "etape3",
        "etape4",
        "etape5",
        "ect"
    ]
}}

Rules:
- temps and temps_cuisson are minutes (integers)
- If missing, guess conservatively
- Provide only the JSON, without any explanation
- type de cuisson can only be: four / poêle / sans cuisson
- type de plat can only be: Entrée / Plat / Dessert / Pain
- pour is an integer that needs to be guessed conservativlyly if missing
- ingredients quantity must keep their unit (g, kg, ml, cl, l, cuillère, pincée, sachet, morceau, tranche, etc)
- french output for each element of the dictionnary

Text:
\"\"\"{text}\"\"\"  
"""
    print(text)

    output = llm(
        prompt,
        max_tokens=800,
        temperature=0.1,
        top_p=1.0
    )

    # Force JSON end
    raw = output["choices"][0]["text"].strip() 
    print("LLM raw output:",raw)
    try:
        result = json.loads(raw)
        print("LLM JSON parsing successful.",result)
    except Exception as e:
        print("LLM JSON parsing error:", e)
        result = None

    # 🚨 FREE MEMORY
    del llm
    gc.collect()

    return result
