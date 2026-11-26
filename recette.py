#!/usr/bin/python3
import os
import pdf_to_recipe
import sys
import customtkinter as ctk
import tkinter as tk
import os
import json 
from PIL import Image
import tkinter as tk
from tkinter import filedialog, messagebox
import copy
import logging
import threading
import time

# Basic logging (no debug output to log.txt)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info('recette.py started')


#color setup:
primary_color = "#FFE8D6"
secondary_color = "#D88C9A"
secondary_hover_color = "#B66D7A"
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("dark-blue")
app = ctk.CTk()
app.title("Recettes")
app.geometry("1450x850")

app.call('tk', 'scaling', 1.5)

# Toggle to show images in recipe display (set to False to hide images but keep code)
SHOW_IMAGES = False
#check if user is on windows and create if non existent appdata folder
if sys.platform.startswith('win'):
    FILE_PATH = 'C:\\Users\\' + os.getlogin() + '\\AppData\\Local\\recette_app\\recette.json'
    FILE_PATH_FAV = 'C:\\Users\\' + os.getlogin() + '\\AppData\\Local\\recette_app\\recette_fav.json'
# File path
else:
    FILE_PATH = "recette.json"
    FILE_PATH_FAV = "recette_fav.json"

# Ensure the JSON file is properly initialized
if sys.platform.startswith('win'):

    if not os.path.exists("C:\\Users\\" + os.getlogin() + "\\AppData\\Local\\recette_app"):
        os.mkdir("C:\\Users\\" + os.getlogin() + "\\AppData\\Local\\recette_app")
    if not os.path.exists("C:\\Users\\" + os.getlogin() + "\\AppData\\Local\\recette_app\\recette.json"):
        initial_data = []
        with open ("C:\\Users\\" + os.getlogin() + "\\AppData\\Local\\recette_app\\recette.        initial_data = []json", "w") as file:
            json.dump(initial_data,file , indent =4, ensure_ascii=False)
else:
    if not os.path.exists("./recette.json"):
        initial_data = []
        with open ("./recette.json", "w") as file:
            json.dump(initial_data,file , indent =4, ensure_ascii=False)
    if not os.path.exists("./image"):
        os.mkdir("./image")


def load_recettes():
    if not os.path.exists(FILE_PATH) or os.path.getsize(FILE_PATH) == 0:
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, indent=4, ensure_ascii=False)
        return []
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
def load_recette_fav():
    if not os.path.exists(FILE_PATH_FAV) or os.path.getsize(FILE_PATH_FAV) == 0:
        with open(FILE_PATH_FAV, "w", encoding = "utf-8") as f:
            json.dump([], f, indent=4, ensure_ascii=False)
        return []
    with open(FILE_PATH_FAV, "r", encoding="utf-8") as f:
        return json.load(f)
    
recette_fav = load_recette_fav()
recettes = load_recettes()
possiblite = []
liste_recette=[]
recette_temp = None

def simplifier_tps(tps_min):
    h = 0
    while tps_min >= 60:
        h += 1
        tps_min -= 60

    if h == 0:
        return f"{tps_min}min"
    return f"{h}h et {tps_min}min"

# Save the updated recipes list
def save_recettes():
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(recettes, f, indent=4, ensure_ascii=False)
    with open(FILE_PATH_FAV,"w", encoding="utf 8") as f:
        json.dump(recette_fav, f, indent = 4 , ensure_ascii=False)
def importer_recette_pdf():
    file_path = filedialog.askopenfilename(
        title="Sélectionner un fichier PDF",
        filetypes=[("PDF files", "*.pdf")]
    )

    if not file_path:
        return  # user cancelled

    # Show a simple loading window with a fake progress bar while parsing runs in background
    loading = ctk.CTk()
    loading.title("Extraction PDF -- patience")
    loading.geometry("420x120")

    lbl = ctk.CTkLabel(loading, text="Extraction du pdf par IA, cela peut prendre quelques secondes...")
    lbl.pack(padx=20, pady=(12, 6))

    progress = ctk.CTkProgressBar(loading, width=360, progress_color=secondary_hover_color)
    progress.set(0.0)
    progress.pack(padx=20, pady=(0, 10))

    result = {}

    def worker():
        try:
            rec = pdf_to_recipe.pdf_to_recipe(file_path)
            # Keep image extraction code (no display if SHOW_IMAGES=False)
            try:
                pdf_to_recipe.extract_images_from_pdf(file_path, f"./img/{rec['nom']}")
            except Exception:
                # extraction error should not break the whole import
                pass
            result['recette'] = rec
        except Exception as e:
            result['error'] = e

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    fake_progress = 0.0

    def animate():
        nonlocal fake_progress
        if thread.is_alive():
            # increase progress slowly, but never reach 100% until done
            fake_progress = min(0.95, fake_progress + 0.02)
            progress.set(fake_progress)
            loading.after(500, animate)
        else:
            progress.set(1.0)
            loading.destroy()
            if 'error' in result:
                messagebox.showerror("Erreur import PDF", str(result['error']))
            else:
                recettes.append(result['recette'])
                save_recettes()
                afficher_accueil()
                recette_variable.configure(text=str(len(recettes)))

    loading.after(100, animate)
    loading.mainloop()

# Function to add a recipe
def ajouter_recette():
    def essayer_ajout_recette():
        nom = entry_nom.get()
        temps_cuisson = entry_temps_cuisson.get()
        personnes = entry_personnes.get()
        type_plat = menu_type_plat.get()
        type_cuisson = menu_type_cuisson.get()
        ingredients_raw = entry_ingredients.get("1.0", tk.END).strip().split("\n")
        temps = entry_temps_prep_repos.get()
        etapes = []
        try:
            temps_cuisson = int(temps_cuisson)
            personnes = int(personnes)
        except ValueError:
            return


        # Parse ingredients
        ingredients = []
        for line in ingredients_raw:
            if ":" in line:
                nom_ing, qty = line.split(":", 1)
                ingredients.append({"nom": nom_ing.strip(), "quantité": qty.strip()})
            else:
                ingredients.append({"nom": line.strip(), "quantité": ""})

        nouvelle_recette = {
            "nom": nom,
            "temps_cuisson": temps_cuisson,
            "temps": temps,
            "pour": personnes,
            "type_plat": type_plat,
            "type_cuisson": type_cuisson,
            "ingredients": ingredients,
            "etapes": etapes
        }

        recettes.append(nouvelle_recette)
        save_recettes()
        
        path_folder = f"./img/{nouvelle_recette['nom']}"
        if not os.path.exists(path_folder):
            os.makedirs(path_folder)
        afficher_accueil()
        recette_variable.configure(text=str(len(recettes)))
        app_ajout_recette.destroy()

    # UI Layout
    app_ajout_recette = ctk.CTk()
    app_ajout_recette.title("Ajout d'une nouvelle recette")
    app_ajout_recette.geometry("500x750")

    ctk.CTkLabel(app_ajout_recette, text="Nom de la recette :").pack(pady=(10, 5))
    entry_nom = ctk.CTkEntry(app_ajout_recette, width=300)
    entry_nom.pack()

    ctk.CTkLabel(app_ajout_recette, text="Temps de cuisson (min) :").pack(pady=(10, 5))
    entry_temps_cuisson = ctk.CTkEntry(app_ajout_recette, width=300)
    entry_temps_cuisson.pack()

    ctk.CTkLabel(app_ajout_recette, text="Temps de préparation").pack(pady=(10, 5))
    entry_temps_prep_repos = ctk.CTkEntry(app_ajout_recette, width=300)
    entry_temps_prep_repos.pack()

    ctk.CTkLabel(app_ajout_recette, text="Nombre de personnes :").pack(pady=(5, 5))
    entry_personnes = ctk.CTkEntry(app_ajout_recette, width=300)
    entry_personnes.pack()

    ctk.CTkLabel(app_ajout_recette, text="Type de plat :").pack(pady=(5, 5))
    menu_type_plat = ctk.CTkOptionMenu(app_ajout_recette, fg_color=secondary_color,button_color=secondary_hover_color,width=300, values=["Entrée", "Plat", "Dessert","Pain"])
    menu_type_plat.pack()
    menu_type_plat.set("Plat")

    ctk.CTkLabel(app_ajout_recette, text="Type de cuisson :").pack(pady=(5, 5))
    menu_type_cuisson = ctk.CTkOptionMenu(app_ajout_recette, fg_color=secondary_color,button_color=secondary_hover_color,width=300, values=["Sans cuisson", "Four", "Poêle"])
    menu_type_cuisson.pack()
    menu_type_cuisson.set("Four")

    ctk.CTkLabel(app_ajout_recette, text="Ingrédients (ex: Farine: 500g)").pack(pady=(5, 5))
    entry_ingredients = ctk.CTkTextbox(app_ajout_recette, height=150, width=300)
    entry_ingredients.pack()

    ctk.CTkButton(app_ajout_recette, fg_color=secondary_color,hover_color=secondary_hover_color,text="Ajouter la recette", command=essayer_ajout_recette).pack(pady=20)

    app_ajout_recette.mainloop()
def isoler_lettre(string):
    final=""
    for i in string:
        if not (i=="0" or i =="1" or i == "2" or i =="3" or i =="4" or i =="5" or i =="6" or i == "7" or i == "8" or i == "9"):
            final += i
    return final 
def isoler_chiffre(string):
    final = "" 
    for i in string:
        if i=="0" or i =="1" or i == "2" or i =="3" or i =="4" or i =="5" or i =="6" or i == "7" or i == "8" or i == "9":
            final += i

    return int(final)


def calc_ingredients(recette, pers):
    global recette_temp

    # Work on a deep copy so we don't mutate the original recipe
    recette_dupli = recette
    pers_base = recette.get("pour", 1)
    print ( f"pers_base: {pers_base}, pers: {pers}")
    if pers_base == 0:
        print("pers_base ==0")
        return

    recette_dupli["pour"] = pers

    for idx, nb_ingr in enumerate(recette_dupli.get("ingredients", [])):
        q = nb_ingr.get("quantité", "")
        try:
            qty_num = isoler_chiffre(q)
            qty_unit = isoler_lettre(q)
            new_qty = str(int(round(qty_num / pers_base * pers))) + qty_unit
        except Exception:
            # If parsing fails, keep original quantity
            new_qty = q
        recette_dupli["ingredients"][idx]["quantité"] = new_qty

    recette_temp = recette_dupli
    
    


def afficher_recette(recette):
    logging.info(f"Affichage de la recette: {recette.get('nom')}")
    try:
        logging.info(f"Ingredients data: {json.dumps(recette.get('ingredients', []), ensure_ascii=False)}")
    except Exception as e:
        logging.info(f"Erreur lors du dump des ingrédients: {e}")

    pour_pers = recette["pour"]
    def pour_change(k):
        nonlocal pour_pers, label_pour_pers
        pour_pers += k
        label_pour_pers.configure(text=f"Pour: {pour_pers}")
        ingredients_label.configure(text=liste_ingredients())

    def liste_ingredients():
        final = ""
        for ingr in recette_temp["ingredients"]:
            final += f'- {ingr["nom"]} ({ingr["quantité"]})\n'
        return final
    def ouvrir_editeur_etapes(recette):
        logging.info(f"Ouverture editeur etapes pour: {recette.get('nom')}")
        editeur = ctk.CTk()
        editeur.title("Éditer les étapes")
        editeur.geometry("500x500")

        ctk.CTkLabel(editeur, text="Entrez les étapes (une par ligne) :").pack(pady=10)
        texte_etapes = ctk.CTkTextbox(editeur, width=400, height=300)
        texte_etapes.pack(padx=10, pady=10)

        # Préremplir si déjà des étapes
        if "etapes" in recette and recette["etapes"]:
            texte_etapes.insert("1.0", "\n".join(recette["etapes"]))

        def sauvegarder_etapes():
            logging.info(f"Sauvegarde étapes pour: {recette.get('nom')}")
            global recettes
            try:
                contenu = texte_etapes.get("1.0", "end").strip()
                nouvelles_etapes = [ligne.strip() for ligne in contenu.split("\n") if ligne.strip()]
                recette["etapes"] = nouvelles_etapes

                # Find and update the original recipe in the global list
                updated = False
                for i, r in enumerate(recettes):
                    if r["nom"] == recette["nom"]:  # Or use a more reliable ID if needed
                        recettes[i] = recette
                        updated = True
                        break

                if not updated:
                    logging.warning(f"Recette non trouvée lors de la sauvegarde des étapes: {recette.get('nom')}")

                save_recettes()
                editeur.destroy()
                afficher_recette(recette)
            except Exception as e:
                logging.exception(f"Erreur lors de la sauvegarde des étapes: {e}")
                messagebox.showerror("Erreur", f"Impossible de sauvegarder les étapes: {e}")



        ctk.CTkButton(editeur, text="Sauvegarder", command=sauvegarder_etapes,fg_color=secondary_color,hover_color=secondary_hover_color).pack(pady=10)
        editeur.mainloop()

    def modifier_recette(recette):
        logging.info(f"Ouverture modificateur pour: {recette.get('nom')}")
        def sauvegarder_modification():
            logging.info(f"Sauvegarde modification demandée pour: {nom_original}")
            nom = entry_nom.get()
            temps_cuisson = entry_temps_cuisson.get()
            personnes = entry_personnes.get()
            type_plat = menu_type_plat.get()
            type_cuisson = menu_type_cuisson.get()
            temps = entry_temps_prep_repos.get()
            ingredients_raw = entry_ingredients.get("1.0", tk.END).strip().split("\n")

            try:
                temps_cuisson = int(temps_cuisson)
                personnes = int(personnes)
            except ValueError:
                messagebox.showerror("Erreur", "Les champs 'Temps de cuisson' et 'Nombre de personnes' doivent être des nombres entiers.")
                return

            try:
                ingredients = []
                for line in ingredients_raw:
                    if ":" in line:
                        nom_ing, qty = line.split(":", 1)
                        ingredients.append({"nom": nom_ing.strip(), "quantité": qty.strip()})
                    elif line.strip():
                        ingredients.append({"nom": line.strip(), "quantité": ""})

                # If the recipe name was changed, try renaming the image folder to keep images associated
                if nom != nom_original:
                    old_folder = f"./img/{nom_original}"
                    new_folder = f"./img/{nom}"
                    try:
                        if os.path.exists(old_folder) and not os.path.exists(new_folder):
                            os.rename(old_folder, new_folder)
                            logging.info(f"Renamed image folder {old_folder} to {new_folder}")
                        elif os.path.exists(new_folder):
                            logging.info(f"New image folder already exists: {new_folder}")
                    except Exception as e:
                        logging.exception(f"Erreur lors du renommage du dossier d'images: {e}")

                recette["nom"] = nom
                recette["temps_cuisson"] = temps_cuisson
                recette["temps"] = temps
                recette["pour"] = personnes
                recette["type_plat"] = type_plat
                recette["type_cuisson"] = type_cuisson
                recette["ingredients"] = ingredients

                # Update the global recipe list
                updated = False
                for i, r in enumerate(recettes):
                    if r["nom"] == nom_original:
                        recettes[i] = recette
                        updated = True
                        break

                if not updated:
                    logging.warning(f"Recette non trouvée lors de la sauvegarde: {nom_original}")

                save_recettes()
                logging.info(f"Sauvegarde réussite pour: {nom}")
                afficher_recette(recette)
                fenetre_modif.destroy()
            except Exception as e:
                logging.exception(f"Erreur lors de la sauvegarde des modifications: {e}")
                messagebox.showerror("Erreur", f"Impossible de sauvegarder la recette: {e}")

        nom_original = recette["nom"]  # Keep track of original name to find and update in list
        fenetre_modif = ctk.CTk()
        fenetre_modif.title("Modifier la recette")
        fenetre_modif.geometry("500x750")

        # UI components
        ctk.CTkLabel(fenetre_modif, text="Nom de la recette :").pack(pady=(10, 5))
        entry_nom = ctk.CTkEntry(fenetre_modif, width=300)
        entry_nom.insert(0, recette["nom"])
        entry_nom.pack()

        ctk.CTkLabel(fenetre_modif, text="Temps de cuisson (min) :").pack(pady=(10, 5))
        entry_temps_cuisson = ctk.CTkEntry(fenetre_modif, width=300)
        entry_temps_cuisson.insert(0, str(recette["temps_cuisson"]))
        entry_temps_cuisson.pack()

        ctk.CTkLabel(fenetre_modif, text="Temps de préparation").pack(pady=(10, 5))
        entry_temps_prep_repos = ctk.CTkEntry(fenetre_modif, width=300)
        entry_temps_prep_repos.insert(0, str(recette["temps"]))
        entry_temps_prep_repos.pack()

        ctk.CTkLabel(fenetre_modif, text="Nombre de personnes :").pack(pady=(5, 5))
        entry_personnes = ctk.CTkEntry(fenetre_modif, width=300)
        entry_personnes.insert(0, str(recette["pour"]))
        entry_personnes.pack()

        ctk.CTkLabel(fenetre_modif, text="Type de plat :").pack(pady=(5, 5))
        menu_type_plat = ctk.CTkOptionMenu(fenetre_modif, width=300,fg_color=secondary_color,button_color=secondary_hover_color,button_hover_color=secondary_color, values=["Entrée", "Plat", "Dessert", "Pain"])
        menu_type_plat.set(recette["type_plat"])
        menu_type_plat.pack()

        ctk.CTkLabel(fenetre_modif, text="Type de cuisson :").pack(pady=(5, 5))
        menu_type_cuisson = ctk.CTkOptionMenu(fenetre_modif, width=300,fg_color=secondary_color,button_color=secondary_hover_color,button_hover_color=secondary_color, values=["Sans cuisson", "Four", "Poêle"])
        menu_type_cuisson.set(recette["type_cuisson"])
        menu_type_cuisson.pack()

        ctk.CTkLabel(fenetre_modif, text="Ingrédients (ex: Farine: 500g)").pack(pady=(5, 5))
        entry_ingredients = ctk.CTkTextbox(fenetre_modif, height=150, width=300)
        for ingr in recette["ingredients"]:
            entry_ingredients.insert("end", f'{ingr["nom"]}: {ingr["quantité"]}\n')
        entry_ingredients.pack()

        sauvegarder_button = ctk.CTkButton(fenetre_modif, text="Sauvegarder les modifications", fg_color=secondary_color,hover_color=secondary_color,command=sauvegarder_modification)
        sauvegarder_button.pack(pady=15,side="right")

        supprimer = ctk.CTkButton(fenetre_modif, text="Supprimer la recette", fg_color=secondary_color, hover_color=secondary_hover_color, command=lambda: (recettes.remove(recette), save_recettes(), afficher_accueil(), recette_variable.configure(text=str(len(recettes))), fenetre_modif.destroy()))
        supprimer.pack(pady=0,side="left", padx=10)
    

        fenetre_modif.mainloop()



    for widget in frame_recette_affichage.winfo_children():
        widget.destroy()

    # Two-column outer layout: col0 small (back), col1 expands (title + content)
    frame_recette_affichage.grid_columnconfigure(0, weight=0)
    frame_recette_affichage.grid_columnconfigure(1, weight=1)
    frame_recette_affichage.grid_rowconfigure(2, weight=1)

    # Top row: back | title container (title + modify button inside)
    btn_back = ctk.CTkButton(frame_recette_affichage, text="<--", hover_color="#B66D7A", fg_color="#D88C9A", width=100, height=25, command=lambda: (afficher_accueil()))
    btn_back.grid(row=0, column=0, padx=5, pady=5)

    frame_titre = ctk.CTkFrame(frame_recette_affichage, height=100, fg_color="#FFE8D6")
    frame_titre.grid(row=0, column=1, sticky="nsew", padx=(0,5), pady=5)

    # Title label left, modify button right inside the title frame so the title area spans full width
    label_titre = ctk.CTkLabel(frame_titre, text=recette["nom"], font=("Helvetica", 45, "bold"))
    label_titre.pack(side="left", fill="both", expand=True, padx=(0,5))

    btn_modifier_recette = ctk.CTkButton(frame_titre, text="Modifier",fg_color="#D88C9A",hover_color="#B66D7A", width=100, height=25, command=lambda: (modifier_recette(recette)))
    btn_modifier_recette.pack(side="right", padx=(5,10), pady=10)
    logging.info(f"Bouton 'Modifier' créé pour: {recette.get('nom')}")

    recette_temp = copy.deepcopy(recette)


    # Content container inside column 1; it will hold two equal columns for ingredients and content
    content_container = ctk.CTkFrame(frame_recette_affichage, fg_color="transparent")
    content_container.grid(row=2, column=0,columnspan=2, sticky="nsew", padx=(0,10), pady=10)
    content_container.grid_columnconfigure(0, weight=1)
    content_container.grid_columnconfigure(1, weight=1)
    content_container.grid_rowconfigure(0, weight=1)

    # Content frames side-by-side inside the container
    ingredients_frame = ctk.CTkFrame(content_container, corner_radius=5, fg_color="#fdf4ee")
    ingredients_frame.grid(row=0, column=0, sticky="nsew", padx=(0,5), pady=0)

    frame_contenu_recette = ctk.CTkFrame(content_container, fg_color="#fdf4ee")
    frame_contenu_recette.grid(row=0, column=1, sticky="nsew", padx=(5,0), pady=0)

    ingredients_title = ctk.CTkLabel(ingredients_frame, text="Ingrédients :", font=("Helvetica", 20, "bold"))
    ingredients_title.pack(anchor="w", padx=10, pady=(10, 5))
    
    # Top controls inside ingredients_frame: -, Pour: X, +
    top_controls = ctk.CTkFrame(ingredients_frame, fg_color="transparent")
    top_controls.pack(fill="x", padx=5, pady=(8, 2))

    btn_moins_pers = ctk.CTkButton(top_controls, text="-", width=50,fg_color=secondary_color, hover_color=secondary_hover_color, height=40, command=lambda: (calc_ingredients(recette, recette["pour"] - 1), pour_change(-1)))
    btn_moins_pers.pack(side="left", padx=(5, 10))

    label_pour_pers = ctk.CTkLabel(top_controls, font=("Helvetica", 16), text=f"Pour: {pour_pers}")
    label_pour_pers.pack(side="left", expand=True)

    btn_plus_pers = ctk.CTkButton(top_controls, text="+", width=50,fg_color=secondary_color, hover_color=secondary_hover_color, height=40, command=lambda: (calc_ingredients(recette, recette["pour"] + 1), pour_change(+1)))
    btn_plus_pers.pack(side="right", padx=(10, 5))

    # Ingredients list below the controls
    ingredients_label = ctk.CTkLabel(ingredients_frame, text=liste_ingredients(), anchor="nw", justify="left")
    ingredients_label.pack(fill="both", expand=True, padx=8, pady=8)

    # Create separate frames for steps and images inside the content area
    steps_frame = ctk.CTkFrame(frame_contenu_recette, fg_color="transparent")
    steps_frame.pack(fill="both", padx=10, pady=(10, 5), anchor="n")

    etapes_title = ctk.CTkLabel(steps_frame, text="Étapes de préparation :", font=("Helvetica", 20, "bold"))
    etapes_title.pack(anchor="w")

    btn_edit_etapes = ctk.CTkButton(steps_frame, fg_color=secondary_color, hover_color=secondary_hover_color, text="Ajouter des étapes" if not recette.get("etapes") else "Modifier les étapes", width=100, height=25, command=lambda: (ouvrir_editeur_etapes(recette)))
    btn_edit_etapes.pack(anchor="ne", padx=10, pady=10) if recette.get("etapes") else btn_edit_etapes.pack(padx=10, pady=10)

    # Afficher les étapes si présentes (inside steps_frame)
    if "etapes" in recette and recette["etapes"]:
        label_etapes = ctk.CTkLabel(steps_frame, text="Étapes de préparation :", font=("Helvetica", 20, "bold"))
        label_etapes.pack(anchor="w", padx=10, pady=(10, 5))

        for i, etape in enumerate(recette["etapes"], 1):
            etape_label = ctk.CTkLabel(steps_frame, text=f"{i}. {etape}", anchor="w", justify="left")
            etape_label.pack(anchor="w", padx=20)

    # --- Affichage des images de la recette (in a dedicated images_frame below steps) ---
    if SHOW_IMAGES:
        dossier_images = f"img/{recette['nom']}"
        if os.path.exists(dossier_images):
            extensions_valides = ('.png', '.jpg', '.jpeg', '.gif')
            images = [f for f in os.listdir(dossier_images) if f.lower().endswith(extensions_valides)]

            if images:
                logging.info(f"Images trouvées pour {recette.get('nom')}: {images}")
                images_frame = ctk.CTkFrame(frame_contenu_recette, fg_color="transparent")
                images_frame.pack(fill="x", padx=10, pady=(10, 10))

                label_images = ctk.CTkLabel(images_frame, text="Photos :", font=("Helvetica", 20, "bold"))
                label_images.pack(anchor="w", padx=10, pady=(5, 5))

                # Create a frame for images arranged horizontally
                frame_images = ctk.CTkFrame(images_frame, fg_color="transparent")
                frame_images.pack(fill="x", padx=10, pady=10)

                for img_file in images:
                    try:
                        img_path = os.path.join(dossier_images, img_file)
                        logging.info(f"Chargement image: {img_path}")
                        img = Image.open(img_path)

                        # Resize the image while maintaining its aspect ratio
                        img.thumbnail((300, 200))  # Resize to fit within 300x200 while keeping aspect ratio
                        photo = ctk.CTkImage(light_image=img, dark_image=img, size=(img.width, img.height))

                        label_img = ctk.CTkLabel(frame_images, image=photo, text="")  # `text=""` to avoid default label text
                        label_img.image = photo  # Keep a reference to avoid garbage collection
                        label_img.pack(side="left", padx=10, pady=10)  # Arrange images horizontally
                    except Exception as e:
                        logging.exception(f"Erreur lors du chargement de l'image {img_file}: {e}")
                        print(f"Erreur lors du chargement de l'image {img_file} : {e}")

    #Add temporary content to see expansion



    # Allow the content area to expand (consistent weights)
    frame_recette_affichage.grid_rowconfigure(2, weight=1)
    frame_recette_affichage.grid_columnconfigure(0, weight=0)
    frame_recette_affichage.grid_columnconfigure(1, weight=1)






def fenetre_recherche():
    global recherche 
    recherche = recettes
    conditions = []

    fenetre = ctk.CTk()
    fenetre.title("Recherche")
    fenetre.geometry("500x750")

    ctk.CTkLabel(fenetre, text="Nom (partiel ou complet)").pack(pady=(10, 5))
    entry_nom = ctk.CTkEntry(fenetre, width=300)
    entry_nom.pack()


    ctk.CTkLabel(fenetre, text="Durée totale (prépa+cuisson)").pack(pady=(5, 5))
    menu_tps = ctk.CTkOptionMenu(fenetre, width=300, values=[" ","30min et moins", "1h et moins ", "1h30 et moins", "2h et moins", "3h et moins", "4h et moins", "+ de 4h"])
    menu_tps.pack()
    menu_tps.set(" ")


    ctk.CTkLabel(fenetre, text="Type de plat :").pack(pady=(5, 5))
    menu_type_plat = ctk.CTkOptionMenu(fenetre, width=300, values=[" ","Entrée", "Plat", "Dessert","Pain"])
    menu_type_plat.pack()
    menu_type_plat.set(" ")


    ctk.CTkLabel(fenetre, text="Type de cuisson :").pack(pady=(5, 5))
    menu_type_cuisson = ctk.CTkOptionMenu(fenetre, width=300, values=[" ","Sans cuisson", "Four", "Poêle"])
    menu_type_cuisson.pack()
    menu_type_cuisson.set(" ")

    ctk.CTkLabel(fenetre, text="Ingrédients (ex: Farine: 500, oeufs)").pack(pady=(5, 5))
    entry_ingredients = ctk.CTkTextbox(fenetre, height=150, width=300)
    entry_ingredients.pack()


    def creer_recherche():
        nonlocal conditions
        global possiblite


        nom = entry_nom.get()
        temps = menu_tps.get()
        type = menu_type_plat.get()
        cuisson = menu_type_cuisson.get()
        ingredients = []
        ingredients_raw = entry_ingredients.get("1.0", tk.END).strip().split("\n")
        for line in ingredients_raw:
            if line.strip():  # ignore les lignes vides
                ingredients.append(line.strip())
        
        conditions = [
            nom if nom else " ",
            temps if temps else " ",
            type if type else " ",
            cuisson if cuisson else " ",
            ingredients
        ]



        possiblite = rechercher()
        afficher_recherche()




    def deja_dans_recherche(i):
        global recherche,possiblite

        for k in recherche:
            if k == recettes[i]:
                return True
        return False 
    def rechercher():
        nonlocal conditions

        recettes = load_recettes()
        recettes_possibles = []

        for recette in recettes:
            correspond = True

            # Condition 0 : Nom
            if conditions[0].strip() and conditions[0].lower() not in recette["nom"].lower():
                correspond = False

            # Condition 1 : Temps total
            if conditions[1] != " ":
                total_temps = int(recette["temps_cuisson"]) + int(recette["temps"])
                limite_temps = {
                    "30min et moins": 30,
                    "1h et moins": 60,
                    "1h30 et moins": 90,
                    "2h et moins": 120,
                    "3h et moins": 180,
                    "4h et moins": 240,
                    "+ de 4h": 9999,  # Arbitrary high value
                }
                if total_temps > limite_temps.get(conditions[1], 9999):
                    if conditions[1] != "+ de 4h" or total_temps <= 240:
                        correspond = False

            # Condition 2 : Type de plat
            if conditions[2] != " " and recette["type_plat"] != conditions[2]:
                correspond = False

            # Condition 3 : Type de cuisson
            if conditions[3] != " " and recette["type_cuisson"] != conditions[3]:
                correspond = False

            # Condition 4 : Ingrédients
            if conditions[4]:
                ingrédients_noms = [i["nom"].lower() for i in recette["ingredients"]]
                for ing in conditions[4]:
                    if ing.lower() not in ingrédients_noms:
                        correspond = False
                        break

            if correspond:
                recettes_possibles.append(recette)

        # Sort recipes: favorites first
        recettes_possibles_sorted = sorted(recettes_possibles, key=lambda r: r in recette_fav, reverse=True)

        fenetre.destroy()
        return recettes_possibles_sorted
                        
    btn_rechercher = ctk.CTkButton(fenetre, text="Rechercher", command =lambda:(creer_recherche()))
    btn_rechercher.pack()


    fenetre.mainloop()
def menu_aide():
    # Ensure the bug report file exists
    if not os.path.exists("log.txt"):
        with open("log.txt", "w", encoding="utf-8") as f:
            f.write("")  # Create an empty file

    # Load the content of the bug report file
    with open("log.txt", "r", encoding="utf-8") as f:
        data_bug = f.read()

    # Create the help window
    aide = ctk.CTk()
    aide.geometry("500x700")

    frame_whole = ctk.CTkFrame(aide, fg_color="grey", corner_radius=15)
    frame_whole.pack(expand=True, fill="both", padx=10, pady=10)

    label_title = ctk.CTkLabel(frame_whole, text="Aide", font=("Poppins", 30, "bold"))
    label_title.pack(pady=10)

    label_textt_box = ctk.CTkTextbox(frame_whole, height=500, width=400, font=("Poppins", 16), fg_color="#FFF8F0")
    label_textt_box.pack(padx=10, pady=10, fill="both", expand=True)
    label_textt_box.insert("1.0", data_bug)

    # Save callback: don't write to disk; just close the window
    def save_bug_report():
        aide.destroy()

    btn_sauvegarder = ctk.CTkButton(frame_whole, text="Fermer", command=save_bug_report)
    btn_sauvegarder.pack(pady=10)

    aide.mainloop()
# Panneau gauche
left_board = ctk.CTkFrame(app, width=400, height=400, corner_radius=15,fg_color="#FFF8F0")
left_board.pack(side="left", fill="y", padx=20, pady=20)
board_title = ctk.CTkLabel(left_board, text="Les recettes ", font=("Poppins", 20, "bold"))
board_title.pack(pady=20)

# Infos pratiques
nb_recette_frame = ctk.CTkFrame(fg_color="#FFE8D6", master=left_board, corner_radius=10)
nb_recette_frame.pack(fill="x", pady=10, padx=10)

recette_label = ctk.CTkLabel(nb_recette_frame, text="Nombre de recettes",bg_color="#FFE8D6", font=("Poppins", 16))
recette_label.pack(side="left", padx=10, pady=10)

recette_variable = ctk.CTkLabel(nb_recette_frame, text=str(len(recettes)), font=("Poppins", 16, "bold"))
recette_variable.pack(side="right", padx=10, pady=10)

def menu_nouvelle_recette():
    fen = ctk.CTk()
    fen.title("Nouvelle recette")
    fen.geometry("300x200")

    ctk.CTkLabel(fen, text="Créer une recette", font=("Poppins", 18, "bold")).pack(pady=20)

    ctk.CTkButton(
        fen,
        text="Créer manuellement",
        fg_color="#D88C9A",
        hover_color="#B66D7A",
        command=lambda: (fen.destroy(), ajouter_recette())
    ).pack(pady=10)

    ctk.CTkButton(
        fen,
        text="Importer depuis un PDF",
        fg_color="#D88C9A",
        hover_color="#B66D7A",
        command=lambda: (fen.destroy(), importer_recette_pdf())
    ).pack(pady=10)

    fen.mainloop()
btn_ajouter_recette = ctk.CTkButton(
    left_board,
    fg_color="#D88C9A",
    hover_color="#B66D7A",
    text="Nouvelle recette",
    font=("Poppins", 16, "bold"),
    text_color="white",
    command=menu_nouvelle_recette
)

btn_ajouter_recette.pack()

btn_chercher_rectte = ctk.CTkButton(left_board,text="Rechercher parmis vos recettes",fg_color="#D88C9A",hover_color="#B66D7A",font=("Poppins", 16, "bold"),text_color="white",command=lambda:(fenetre_recherche()))
btn_chercher_rectte.pack(pady = 10)
    
#partie droite

right_board = ctk.CTkFrame(app, width=400, height = 400, corner_radius=15, fg_color="#FFF8F0")
right_board.pack(side="left",expand=True, fill="both", padx=5, pady=20)

frame_recherche = ctk.CTkFrame(right_board,width=50, height = 50, corner_radius=15,fg_color="#FFE8D6")
frame_recherche.pack(anchor="n",expand = True, fill = "x",padx=5,pady=5)

label_titre = ctk.CTkLabel(frame_recherche,text="MAMARMITON (v3.0)", font=("Helvetica", 50, "bold"))
label_titre.pack(padx=5,pady=5)  
help_btn = ctk.CTkButton(frame_recherche,text="Aide ?",fg_color="green",command=lambda:(menu_aide()))
help_btn.pack(anchor="ne",padx=5,pady=5)

frame_recette_affichage = ctk.CTkScrollableFrame(right_board,width=400, height =1000, fg_color="#FFF8F0",corner_radius = 15)
frame_recette_affichage.pack( expand = True, fill ="both",padx=10,pady=10)

def ajouter_fav(temp):
    global recette_fav, recettes
    for recette_fa in recette_fav:
        if recette_fa == recettes[temp]:
            recette_fav.pop(temp) 
            save_recettes()
            return
    recette_fav.append(recettes[temp])
    save_recettes()
    

def afficher_accueil():
    recettes = load_recettes()
    global liste_recette

    # Sort recipes: favorites first
    recettes_sorted = sorted(recettes, key=lambda r: r in recette_fav, reverse=True)

    for widget in frame_recette_affichage.winfo_children():
        widget.destroy()
    liste_recette = []
    for recette_1 in recettes_sorted:
        liste_recette.append(recette_1)
    compteur = 0

    def star_ou_pas(temp):
        for recette_fa in recette_fav:
            if recette_fa == recettes[temp]:
                return "retirer des favoris"
        return "ajouter au favoris"

    for recette in recettes_sorted:
        if not recette:
            break

        recette_rapide = ctk.CTkFrame(frame_recette_affichage, corner_radius=15, height=200, width=1000, fg_color="#FFE8D6")
        recette_rapide.grid(row=compteur // 2, column=compteur % 2, padx=25, pady=20, sticky="nsew")
        recette_rapide.grid_propagate(False)
        recette_rapide.grid_rowconfigure(0, weight=1)
        recette_rapide.grid_columnconfigure(0, weight=1)

        # Pass the current value of compteur explicitly to the lambda function
        '''btn_fav = ctk.CTkButton(
            recette_rapide,
            text=star_ou_pas(compteur),
            fg_color="transparent",
            hover_color="#FFE8D6",
            font=("roboto", 16),
            text_color="black",
            border_color="black",
            command=lambda temp=compteur: (ajouter_fav(temp), btn_fav.configure(text=star_ou_pas(temp))),
        )'''
        
        nom_affichage = ctk.CTkLabel(recette_rapide, text=recette["nom"], font=("Poppins", 20, "bold"))
        tps_affichage = ctk.CTkLabel(recette_rapide, text=simplifier_tps(int(recette["temps_cuisson"]) + int(recette["temps"])))
        type_affichage = ctk.CTkLabel(recette_rapide, text=recette["type_plat"])
        tps_dissoce = ctk.CTkLabel(
            recette_rapide, text=f"Prépa: {recette['temps']} Cuisson: {recette['temps_cuisson']}", font=("Poppins", 13)
        )
        btn_inspecter_recette = ctk.CTkButton(
            recette_rapide,
            fg_color="#D88C9A",
            hover_color="#B66D7A",
            text="Préparer!",
            command=lambda temp=compteur: ( afficher_recette(liste_recette[temp])),
        )

        nom_affichage.pack(pady=7)
        #btn_fav.pack(anchor="ne", padx=3, pady=3)
        tps_affichage.pack()
        tps_dissoce.pack(pady=2)
        type_affichage.pack(pady=5)
        btn_inspecter_recette.pack(pady=5)
        compteur += 1
    compteur = 0
    for recette in recettes_sorted:
        #btn_fav.configure(text=star_ou_pas(compteur))
        compteur += 1
    
    for col in range(2):
        frame_recette_affichage.grid_columnconfigure(col, weight=1)
    height = frame_recette_affichage.winfo_height()
    width = frame_recette_affichage.winfo_width()


    

def afficher_recherche():
    global possiblite

    for widget in frame_recette_affichage.winfo_children():
        widget.destroy()
    liste_recette = []

    for recette_1 in possiblite:
        liste_recette.append(recette_1)

    compteur = 0
    btn_back = ctk.CTkButton(frame_recette_affichage,height=20,width = 60,text="<--",bg_color = "#D88C9A",fg_color="#D88C9A",hover_color="#B66D7A",command=lambda:(afficher_accueil()))
    btn_back.grid(padx=5,pady=5,row=0,column=0)

    for recette in possiblite:
        
        recette_rapide = ctk.CTkFrame(frame_recette_affichage,corner_radius=15, height = 200,fg_color="#FFE8D6")
        recette_rapide.grid(row=(compteur // 3) + 1, column=compteur % 3, padx=20, pady=20, sticky="nsew")  
        recette_rapide.grid_propagate(False)  
        recette_rapide.grid_rowconfigure(0, weight=1)
        recette_rapide.grid_columnconfigure(0, weight = 1)

        nom_affichage = ctk.CTkLabel(recette_rapide,text=recette["nom"],font = ("Poppins", 20, "bold"))
        tps_affichage = ctk.CTkLabel(recette_rapide,text = simplifier_tps(int(recette["temps_cuisson"]) + int(recette["temps"])))
        type_affichage = ctk.CTkLabel(recette_rapide,text=recette["type_plat"])
        tps_dissoce = ctk.CTkLabel(recette_rapide, text = f"Prépa: {recette['temps']} Cuisson: {recette['temps_cuisson']}", font = ("Poppins", 13))
        btn_inspecter_recette = ctk.CTkButton(recette_rapide,fg_color="#D88C9A",hover_color="#B66D7A", text="Préparer!", command = lambda temp=compteur: (afficher_recette(liste_recette[temp])))

        nom_affichage.pack(pady = 7)
        tps_affichage.pack()
        tps_dissoce.pack(pady=2)
        type_affichage.pack(pady=5)
        btn_inspecter_recette.pack()
        compteur += 1
    for col in range (3):
        frame_recette_affichage.grid_columnconfigure(col, weight = 1)

    




    


afficher_accueil()

app.mainloop()

