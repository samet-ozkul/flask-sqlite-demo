"""🍲 Tarifler: malzemeler, hazırlanış (Markdown), fotoğraf ve alışveriş listesine aktarma."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..storage import attachments_for, delete_for, first_thumbs
from ..utils import form_str, normalize_tags
from .lists import TEXT_MAX, accessible_lists, add_items, list_or_404

bp = Blueprint("recipes", __name__, url_prefix="/tarifler")

DEFAULT_LIST_NAME = "Alışveriş"


def parse_ingredients(text):
    """Her satır bir malzeme. '#' ile başlayan ya da ':' ile biten kısa satırlar bölüm başlığıdır."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or (line.endswith(":") and len(line) <= 60):
            header = line.lstrip("#").strip().rstrip(":").strip()
            if header:
                out.append({"text": header, "header": True})
            continue
        line = line.lstrip("-*•·").strip()
        if line:
            out.append({"text": line, "header": False})
    return out


def _form_values():
    return {
        "title": form_str("title", 150),
        "servings": form_str("servings", 50),
        "tags": normalize_tags(form_str("tags", 300)),
        "ingredients": form_str("ingredients", 10000),
        "steps": form_str("steps", 30000),
    }


def _owner_label(lst, user_id):
    if lst["user_id"] == user_id:
        return lst["name"]
    return f"{lst['name']} ({lst['owner_display'] or lst['owner_username']})"


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    q = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip().lower()
    sql = "SELECT * FROM recipes WHERE user_id = ?"
    args = [uid]
    if q:
        sql += " AND (title LIKE ? OR ingredients LIKE ? OR tags LIKE ? OR steps LIKE ?)"
        args += [f"%{q}%"] * 4
    if tag:
        sql += " AND (', ' || tags || ',') LIKE ?"
        args.append(f"%, {tag},%")
    sql += " ORDER BY title COLLATE NOCASE, id"
    recipes = query(sql, args)
    thumbs = first_thumbs("recipe", [r["id"] for r in recipes])
    ing_counts = {r["id"]: sum(1 for i in parse_ingredients(r["ingredients"]) if not i["header"])
                  for r in recipes}
    all_tags = sorted({t for r in query("SELECT tags FROM recipes WHERE user_id = ?", (uid,))
                       for t in (r["tags"] or "").split(", ") if t})
    return render_template("recipes/index.html", recipes=recipes, thumbs=thumbs, ing_counts=ing_counts,
                           q=q, tag=tag, all_tags=all_tags)


@bp.route("/yeni", methods=["GET", "POST"])
@login_required
def create():
    v = {"title": "", "servings": "", "tags": "", "ingredients": "", "steps": ""}
    if request.method == "POST":
        v = _form_values()
        if not v["title"]:
            flash("Tarif adı gerekli.", "warning")
        else:
            recipe_id = execute(
                "INSERT INTO recipes (user_id, title, servings, tags, ingredients, steps, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (g.user["id"], v["title"], v["servings"], v["tags"], v["ingredients"], v["steps"]),
            ).lastrowid
            flash("Tarif kaydedildi. İstersen fotoğraf ekleyebilirsin.", "success")
            return redirect(url_for("recipes.detail", recipe_id=recipe_id))
    return render_template("recipes/edit.html", recipe=None, v=v)


@bp.route("/<int:recipe_id>")
@login_required
def detail(recipe_id):
    uid = g.user["id"]
    recipe = owned_or_404("recipes", recipe_id, uid)
    shopping = accessible_lists(uid, "shopping")
    return render_template(
        "recipes/detail.html", recipe=recipe, ingredients=parse_ingredients(recipe["ingredients"]),
        list_options=[(l["id"], _owner_label(l, uid)) for l in shopping],
        files=attachments_for("recipe", recipe_id),
    )


@bp.route("/<int:recipe_id>/duzenle", methods=["GET", "POST"])
@login_required
def edit(recipe_id):
    uid = g.user["id"]
    recipe = owned_or_404("recipes", recipe_id, uid)
    v = recipe
    if request.method == "POST":
        v = _form_values()
        if not v["title"]:
            flash("Tarif adı gerekli.", "warning")
        else:
            execute(
                "UPDATE recipes SET title = ?, servings = ?, tags = ?, ingredients = ?, steps = ?,"
                " updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                (v["title"], v["servings"], v["tags"], v["ingredients"], v["steps"], recipe_id, uid),
            )
            flash("Tarif güncellendi.", "success")
            return redirect(url_for("recipes.detail", recipe_id=recipe_id))
    return render_template("recipes/edit.html", recipe=recipe, v=v)


@bp.route("/<int:recipe_id>/alisveris", methods=["POST"])
@login_required
def to_list(recipe_id):
    """Seçilen malzemeleri erişilebilir bir alışveriş listesine ekler (yoksa 'Alışveriş' listesi açar)."""
    uid = g.user["id"]
    recipe = owned_or_404("recipes", recipe_id, uid)
    ingredients = parse_ingredients(recipe["ingredients"])
    picked = {int(x) for x in request.form.getlist("ing") if x.isdigit()}
    texts = [ing["text"][:TEXT_MAX] for i, ing in enumerate(ingredients) if i in picked and not ing["header"]]
    if not texts:
        flash("Eklenecek malzeme seçilmedi.", "warning")
        return redirect(url_for("recipes.detail", recipe_id=recipe_id))

    list_id = request.form.get("list_id", type=int)
    if list_id:
        lst = list_or_404(list_id, uid)
        if lst["kind"] != "shopping":
            abort(400, "Seçilen liste bir alışveriş listesi değil.")
        list_id, list_name = lst["id"], lst["name"]
    else:
        existing = accessible_lists(uid, "shopping")
        if existing:
            list_id, list_name = existing[0]["id"], existing[0]["name"]
        else:
            list_name = DEFAULT_LIST_NAME
            list_id = execute(
                "INSERT INTO lists (user_id, name, kind) VALUES (?, ?, 'shopping')", (uid, list_name)
            ).lastrowid

    # Listede zaten açık olan malzemeleri tekrar ekleme
    seen = {r["text"].casefold() for r in query(
        "SELECT text FROM list_items WHERE list_id = ? AND done = 0", (list_id,))}
    new = []
    for text in texts:
        if text.casefold() not in seen:
            seen.add(text.casefold())
            new.append(text)
    add_items(list_id, new, uid)
    msg = f"{len(new)} malzeme “{list_name}” listesine eklendi."
    if len(new) < len(texts):
        msg += f" {len(texts) - len(new)} tanesi zaten listedeydi."
    flash(msg, "success" if new else "warning")
    return redirect(url_for("recipes.detail", recipe_id=recipe_id))


@bp.route("/<int:recipe_id>/sil", methods=["POST"])
@login_required
def delete(recipe_id):
    uid = g.user["id"]
    recipe = owned_or_404("recipes", recipe_id, uid)
    delete_for("recipe", recipe_id)
    execute("DELETE FROM recipes WHERE id = ? AND user_id = ?", (recipe_id, uid))
    flash(f"“{recipe['title']}” tarifi silindi.", "success")
    return redirect(url_for("recipes.index"))
