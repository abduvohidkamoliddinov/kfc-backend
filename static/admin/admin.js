const API = ""; // agar backend boshqa domen bo‘lsa: "https://domain.com"

const el = (id) => document.getElementById(id);
const modal = el("modal");
const modalTitle = el("modalTitle");
const modalBody = el("modalBody");

let menuData = null;

function openModal(title, html) {
  modalTitle.textContent = title;
  modalBody.innerHTML = html;
  modal.classList.add("open");
}
function closeModal() {
  modal.classList.remove("open");
  modalBody.innerHTML = "";
}
el("modalClose").onclick = closeModal;
modal.onclick = (e) => { if (e.target === modal) closeModal(); };

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: {"Content-Type":"application/json", ...(opts.headers||{})},
    ...opts
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function loadMenu() {
  menuData = await api("/api/menu");
  renderCategories();
  renderItems();
  fillCategorySelect();
}

function renderCategories() {
  const list = el("catList");
  list.innerHTML = "";

  (menuData.categories || []).forEach(c => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <div class="meta">
        <div><b>${c.name_uz}</b> <span class="badge">${c.slug}</span></div>
        <div class="small">RU: ${c.name_ru}</div>
      </div>
      <div class="actions">
        <button class="dot" data-edit="${c.slug}">⋯ Edit</button>
        <button class="dot" data-del="${c.slug}">Delete</button>
      </div>
    `;
    list.appendChild(div);
  });

  list.querySelectorAll("[data-edit]").forEach(btn => {
    btn.onclick = () => openEditCategory(btn.dataset.edit);
  });
  list.querySelectorAll("[data-del]").forEach(btn => {
    btn.onclick = async () => {
      const slug = btn.dataset.del;
      if (!confirm("Category o‘chsinmi? Ichidagi itemlar ham o‘chadi.")) return;
      await api(`/api/menu/categories/${slug}`, { method: "DELETE" });
      await loadMenu(); // select ham shu bilan yangilanadi ✅
    };
  });
}

function fillCategorySelect() {
  const s = el("itemCatSelect");
  s.innerHTML = `<option value="">Barcha</option>`;
  (menuData.categories || []).forEach(c => {
    const opt = document.createElement("option");
    opt.value = c.slug;
    opt.textContent = c.name_uz;
    s.appendChild(opt);
  });
}

function renderItems() {
  const list = el("itemList");
  const cat = el("itemCatSelect").value;
  const q = (el("itemSearch").value || "").toLowerCase();

  let items = menuData.items || [];
  if (cat) items = items.filter(i => i.category_slug === cat);
  if (q) items = items.filter(i =>
    (i.name_uz||"").toLowerCase().includes(q) ||
    (i.name_ru||"").toLowerCase().includes(q)
  );

  list.innerHTML = "";
  items.forEach(i => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <div class="meta">
        <div><b>${i.name_uz}</b> <span class="badge">${i.price} so‘m</span></div>
        <div class="small">Cat: ${i.category_slug} • RU: ${i.name_ru}</div>
        ${i.image_url ? `<div class="small">Img: ${i.image_url}</div>` : `<div class="small">Img: yo‘q</div>`}
      </div>
      <div class="actions">
        <button class="dot" data-edit-item="${i.id}">⋯ Edit</button>
        <button class="dot" data-del-item="${i.id}">Delete</button>
      </div>
    `;
    list.appendChild(div);
  });

  list.querySelectorAll("[data-edit-item]").forEach(btn => {
    btn.onclick = () => openEditItem(parseInt(btn.dataset.editItem,10));
  });
  list.querySelectorAll("[data-del-item]").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.delItem;
      if (!confirm("Item o‘chsinmi?")) return;
      await api(`/api/menu/items/${id}`, { method: "DELETE" });
      await loadMenu();
    };
  });
}

el("itemCatSelect").onchange = renderItems;
el("itemSearch").oninput = renderItems;

el("btnAddCat").onclick = () => {
  openModal("Add Category", `
    <div class="form">
      <div>
        <label>Name UZ</label>
        <input id="c_name_uz" placeholder="Burgerlar" />
      </div>
      <div>
        <label>Name RU</label>
        <input id="c_name_ru" placeholder="Бургеры" />
      </div>
      <div>
        <label>Slug (ixtiyoriy)</label>
        <input id="c_slug" placeholder="burgers" />
      </div>
      <div class="bar">
        <button class="dot" onclick="closeModal()">Cancel</button>
        <button class="btn" id="c_save">Save</button>
      </div>
    </div>
  `);

  document.getElementById("c_save").onclick = async () => {
    const name_uz = document.getElementById("c_name_uz").value.trim();
    const name_ru = document.getElementById("c_name_ru").value.trim();
    const slug = document.getElementById("c_slug").value.trim();
    if (!name_uz) return alert("Name UZ kerak");
    await api("/api/menu/categories", {
      method:"POST",
      body: JSON.stringify({ name_uz, name_ru: name_ru||null, slug: slug||null })
    });
    closeModal();
    await loadMenu();
  };
};

function openEditCategory(slug) {
  const c = menuData.categories.find(x => x.slug === slug);
  openModal("Edit Category", `
    <div class="form">
      <div><label>Name UZ</label><input id="ec_name_uz" value="${c.name_uz||""}"/></div>
      <div><label>Name RU</label><input id="ec_name_ru" value="${c.name_ru||""}"/></div>
      <div class="bar">
        <button class="dot" onclick="closeModal()">Cancel</button>
        <button class="btn" id="ec_save">Save</button>
      </div>
    </div>
  `);
  document.getElementById("ec_save").onclick = async () => {
    const name_uz = document.getElementById("ec_name_uz").value.trim();
    const name_ru = document.getElementById("ec_name_ru").value.trim();
    if (!name_uz) return alert("Name UZ kerak");
    await api(`/api/menu/categories/${slug}`, {
      method:"PUT",
      body: JSON.stringify({ name_uz, name_ru: name_ru||null })
    });
    closeModal();
    await loadMenu();
  };
}

el("btnAddItem").onclick = () => {
  const opts = (menuData.categories||[]).map(c => `<option value="${c.slug}">${c.name_uz}</option>`).join("");
  openModal("Add Item", `
    <div class="form">
      <div><label>Category</label><select id="i_cat">${opts}</select></div>
      <div class="row2">
        <div><label>Name UZ</label><input id="i_name_uz"/></div>
        <div><label>Name RU</label><input id="i_name_ru"/></div>
      </div>
      <div class="row2">
        <div><label>Price</label><input id="i_price" type="number" value="0"/></div>
        <div><label>Image</label><input id="i_img" type="file" accept="image/*"/></div>
      </div>
      <div><label>Desc UZ</label><textarea id="i_desc_uz"></textarea></div>
      <div><label>Desc RU</label><textarea id="i_desc_ru"></textarea></div>
      <div class="bar">
        <button class="dot" onclick="closeModal()">Cancel</button>
        <button class="btn" id="i_save">Save</button>
      </div>
    </div>
  `);

  document.getElementById("i_save").onclick = async () => {
    const category_slug = document.getElementById("i_cat").value;
    const name_uz = document.getElementById("i_name_uz").value.trim();
    const name_ru = document.getElementById("i_name_ru").value.trim();
    const price = parseInt(document.getElementById("i_price").value, 10) || 0;
    const desc_uz = document.getElementById("i_desc_uz").value || "";
    const desc_ru = document.getElementById("i_desc_ru").value || "";

    if (!name_uz || !name_ru) return alert("Name UZ va RU kerak");

    // upload
    let image_url = null;
    const f = document.getElementById("i_img").files[0];
    if (f) {
      const fd = new FormData();
      fd.append("file", f);
      const up = await fetch(API + "/api/upload", { method:"POST", body: fd });
      if (!up.ok) throw new Error(await up.text());
      const upj = await up.json();
      image_url = upj.url;
    }

    await api("/api/menu/items", {
      method:"POST",
      body: JSON.stringify({ category_slug, name_uz, name_ru, price, image_url, desc_uz, desc_ru })
    });

    closeModal();
    await loadMenu();
  };
};

function openEditItem(id) {
  const i = menuData.items.find(x => x.id === id);
  const opts = (menuData.categories||[])
    .map(c => `<option value="${c.slug}" ${c.slug===i.category_slug?"selected":""}>${c.name_uz}</option>`).join("");

  openModal("Edit Item", `
    <div class="form">
      <div><label>Category</label><select id="ei_cat">${opts}</select></div>
      <div class="row2">
        <div><label>Name UZ</label><input id="ei_name_uz" value="${i.name_uz||""}"/></div>
        <div><label>Name RU</label><input id="ei_name_ru" value="${i.name_ru||""}"/></div>
      </div>
      <div class="row2">
        <div><label>Price</label><input id="ei_price" type="number" value="${i.price||0}"/></div>
        <div><label>New Image (ixtiyoriy)</label><input id="ei_img" type="file" accept="image/*"/></div>
      </div>
      <div class="small">Old img: ${i.image_url||"yo‘q"}</div>
      <div><label>Desc UZ</label><textarea id="ei_desc_uz">${i.desc_uz||""}</textarea></div>
      <div><label>Desc RU</label><textarea id="ei_desc_ru">${i.desc_ru||""}</textarea></div>
      <div class="bar">
        <button class="dot" onclick="closeModal()">Cancel</button>
        <button class="btn" id="ei_save">Save</button>
      </div>
    </div>
  `);

  document.getElementById("ei_save").onclick = async () => {
    const patch = {
      category_slug: document.getElementById("ei_cat").value,
      name_uz: document.getElementById("ei_name_uz").value.trim(),
      name_ru: document.getElementById("ei_name_ru").value.trim(),
      price: parseInt(document.getElementById("ei_price").value,10) || 0,
      desc_uz: document.getElementById("ei_desc_uz").value || "",
      desc_ru: document.getElementById("ei_desc_ru").value || ""
    };

    // optional upload
    const f = document.getElementById("ei_img").files[0];
    if (f) {
      const fd = new FormData();
      fd.append("file", f);
      const up = await fetch(API + "/api/upload", { method:"POST", body: fd });
      if (!up.ok) throw new Error(await up.text());
      const upj = await up.json();
      patch.image_url = upj.url;
    }

    await api(`/api/menu/items/${id}`, { method:"PATCH", body: JSON.stringify(patch) });
    closeModal();
    await loadMenu();
  };
}

loadMenu().catch(err => alert("Admin error: " + err.message));
