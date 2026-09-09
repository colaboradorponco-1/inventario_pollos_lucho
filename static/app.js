const API = '/api';
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const on = (sel, ev, fn) => { const el = $(sel); if (el) el.addEventListener(ev, fn); };
const fmtNum = (n) => Number(n ?? 0).toLocaleString('es-PE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = (d) => {
    if (!d) return '—';
    const parts = d.split(' ');
    const datePart = parts[0] || '';
    const timePart = parts[1] || '';
    const dp = datePart.split('-');
    if (dp.length !== 3) return d;
    const formatted = dp[2] + '/' + dp[1] + '/' + dp[0];
    return timePart ? formatted + ' ' + timePart.substring(0, 5) : formatted;
};

let catalogos = { almacenes: [], categorias: [], proveedores: [] };

function nowLocal() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0') + 'T' + String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
}

function fechaISO(val) {
    if (!val) return '';
    return val.replace('T', ' ') + ':00';
}

const esc = (s) => { const d = document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; };

const nomProd = (p) => esc(p.nombre) + (p.marca ? ` - ${esc(p.marca)}` : '');

async function request(url, opts = {}) {
    const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.message || 'Error del servidor');
    const data = json.data;
    if (data !== null && data !== undefined && typeof data === 'object') {
        try {
            if (Object.prototype.hasOwnProperty.call(json, 'total')) data.total = json.total;
            if (Object.prototype.hasOwnProperty.call(json, 'pagina')) data.pagina = json.pagina;
            if (Object.prototype.hasOwnProperty.call(json, 'por_pagina')) data.por_pagina = json.por_pagina;
            if (Object.prototype.hasOwnProperty.call(json, 'message')) data.message = json.message;
        } catch (_) { /* sin datos */ }
    }
    return data ?? { message: json.message || '' };
}

function toast(msg, type = 'ok') {
    const t = $('#toast');
    t.textContent = msg;
    t.className = `toast show ${type}`;
    setTimeout(() => t.classList.remove('show'), 3000);
}

// ---------------- Navegación ----------------
function cerrarMenu() {
    $('#sidebar').classList.remove('open');
    const overlay = $('#sidebar-overlay');
    if (overlay) overlay.classList.remove('open');
}

$('#btn-menu').addEventListener('click', () => {
    $('#sidebar').classList.toggle('open');
    const overlay = $('#sidebar-overlay');
    if (overlay) overlay.classList.toggle('open');
});

$$('.menu-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
        $$('.menu-btn').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        $$('.view').forEach((v) => v.classList.remove('active'));
        $('#view-' + btn.dataset.view).classList.add('active');
        loadView(btn.dataset.view);
        cerrarMenu();
    });
});

function loadView(name) {
    if (name === 'dashboard') loadDashboard();
    if (name === 'productos') { pintarProdScope(); loadProductos(); }
    if (name === 'movimientos') loadMovimientos();
    if (name === 'proveedores') loadProveedores();
    if (name === 'gastos') loadGastos();
    if (name === 'reportes') loadReportes();
    if (name === 'ventas') loadVentas();
    if (name === 'repartos') loadRepartos();
    if (name === 'pedidos') loadPedidos();
    if (name === 'usuarios') loadUsuarios();
    if (name === 'auditoria') loadAuditoria();
    if (name === 'almacenes') loadAlmacenes();
    if (name === 'categorias') loadCategorias();
}

// ---------------- Catálogos ----------------
async function loadCatalogos() {
    catalogos = await request(API + '/catalogos');

    const fill = (sel, items, placeholder, nameKey = 'nombre') => {
        const el = $(sel);
        if (!el) return null;
        el.innerHTML = '<option value="">' + placeholder + '</option>' +
            items.map((i) => `<option value="${i.id}">${i[nameKey]}</option>`).join('');
        return el;
    };
    fill('#prod-categoria', catalogos.categorias, 'Todas las categorías');
    fill('#prod-categoria-form', catalogos.categorias, '— Sin categoría —');
    fill('#prod-proveedor', catalogos.proveedores, '— Sin proveedor —');
    fill('#prod-filtro-proveedor', catalogos.proveedores, 'Todos los proveedores');
    fill('#prov-sucursal', catalogos.sucursales, '— Sin sucursal —');
    const sucursalesOpt = () => {
        const lista = esCentral()
            ? (catalogos.sucursales || [])
            : (catalogos.sucursales || []).filter((s) => s.id === window.SUCURSAL_ID);
        return lista.map((i) => `<option value="${i.id}">${i.nombre}</option>`).join('');
    };
    const selImp = $('#importar-sucursal');
    if (selImp) selImp.innerHTML = '<option value="">Seleccione una sucursal...</option>' + sucursalesOpt();
    const selExp = $('#exportar-sucursal');
    if (selExp) selExp.innerHTML = '<option value="">Todas las sucursales</option>' + sucursalesOpt();
    fill('#prod-almacen', catalogos.almacenes, '— Sin almacén —');
    fill('#prod-sucursal', catalogos.sucursales, 'Seleccione una sucursal...');
    fill('#importar-categoria', catalogos.categorias, '— Sin categoría —');
    fill('#exportar-categoria', catalogos.categorias, 'Todas las categorías');
    fill('#mov-almacen', catalogos.almacenes, '— Sin almacén —');
    fill('#prod-proveedor', catalogos.proveedores, '— Sin proveedor —');
    fill('#gasto-proveedor', catalogos.proveedores, '— Sin proveedor —');
    fill('#mov-proveedor', catalogos.proveedores, '— Sin proveedor —');
    fill('#qr-proveedor', catalogos.proveedores, 'Proveedor (opcional)');
    fill('#prov-sucursal', catalogos.sucursales, '— Sin sucursal —');
    fill('#prov-sucursal-select', catalogos.sucursales, 'Seleccione una sucursal...');
    fill('#gasto-sucursal-select', catalogos.sucursales, 'Seleccione una sucursal...');
    fill('#venta-sucursal-select', catalogos.sucursales, 'Seleccione una sucursal...');
    fill('#reparto-sucursal-select', catalogos.sucursales, 'Seleccione una sucursal...');
}

// ---------------- Dashboard ----------------
async function loadDashboard() {
    try {
        const d = await request(API + '/dashboard');
        $('#stat-productos').textContent = d.total_productos;
        $('#stat-stock').textContent = d.stock_total;
        $('#stat-valor').textContent = 'Bs ' + fmtNum(d.valor_inventario);
        $('#stat-stock-bajo').textContent = (d.stock_bajo || []).length;
        $('#stat-ventas-hoy').textContent = 'Bs ' + fmtNum(d.ventas_hoy);
        $('#stat-gastos-hoy').textContent = 'Bs ' + fmtNum(d.gastos_hoy);
        $('#stat-utilidad-hoy').textContent = 'Bs ' + fmtNum(d.utilidad_hoy);
        $('#stat-num-ventas-hoy').textContent = d.num_ventas_hoy || 0;
        $('#stat-repartos-hoy').textContent = d.repartos_hoy || 0;
        $('#stat-ventas').textContent = 'Bs ' + fmtNum(d.ventas_mes);
        $('#stat-gastos').textContent = 'Bs ' + fmtNum(d.gastos_mes);
        $('#stat-utilidad').textContent = 'Bs ' + fmtNum(d.utilidad_mes);
        $('#stat-repartos').textContent = d.repartos_mes || 0;
        $('#stat-ventas-anio').textContent = 'Bs ' + fmtNum(d.ventas_anio);
        $('#stat-gastos-anio').textContent = 'Bs ' + fmtNum(d.gastos_anio);
        $('#stat-utilidad-anio').textContent = 'Bs ' + fmtNum(d.utilidad_anio);
        $('#stat-num-ventas-anio').textContent = d.num_ventas_anio || 0;
        $('#stat-repartos-anio').textContent = d.repartos_anio || 0;
        $('#stat-num-ventas-mes').textContent = d.num_ventas_mes || 0;
        $('#stat-repartos').textContent = d.repartos_mes || 0;

        $('#dash-welcome').textContent = (window.SUCURSAL ? `Panel de ${window.SUCURSAL}` : 'Resumen del inventario') +
            ` · ${new Date().toLocaleDateString('es-PE', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}`;

        const t1 = $('#dash-movimientos');
        t1.innerHTML = (d.mov_recientes || []).length ? (d.mov_recientes || []).map((m) => `
            <tr>
                <td>${fmtDate(m.fecha)}</td>
                <td><strong>${m.producto_nombre}</strong></td>
                <td><span class="badge badge-${m.tipo}">${m.tipo === 'entrada' ? 'Entrada' : 'Salida'}</span></td>
                <td>${m.cantidad} ${m.unidad}</td>
                <td>${m.nota || ''}</td>
            </tr>`).join('')
            : '<tr><td colspan="5" class="empty">Sin movimientos registrados</td></tr>';

        const stockBajo = d.stock_bajo || [];
        const porVencer = d.por_vencer || [];

        const tStock = $('#dash-alertas-stock');
        tStock.innerHTML = stockBajo.length ? stockBajo.map((p) => {
            const estado = p.stock === 0 ? 'Sin stock' : 'Bajo';
            return `<tr>
                <td><strong>${nomProd(p)}</strong><br><small style="color:var(--muted)">${esc(p.codigo || '')}</small></td>
                <td style="font-weight:700;color:${p.stock === 0 ? '#dc2626' : '#d97706'}">${p.stock} ${p.unidad}</td>
                <td>${p.stock_minimo || 10} ${p.unidad}</td>
                <td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.stock === 0 ? '#dc2626' : '#d97706'};margin-right:6px"></span>${estado}</td>
            </tr>`;
        }).join('') : '<tr><td colspan="4" class="empty">No hay alertas de stock</td></tr>';

        const tVenc = $('#dash-alertas-vencimiento');
        tVenc.innerHTML = porVencer.length ? porVencer.map((p) => {
            let estado, color;
            if (p.estado === 'vencido') { estado = 'VENCIDO'; color = '#dc2626'; }
            else if (p.estado === 'urgente') { estado = 'Urgente'; color = '#d97706'; }
            else { estado = 'Proximo'; color = '#ca8a04'; }
            return `<tr>
                <td><strong>${nomProd(p)}</strong></td>
                <td style="font-weight:700;color:${color}">${fmtDate(p.vencimiento)}</td>
                <td>${p.stock} ${p.unidad}</td>
                <td><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px"></span>${estado}</td>
            </tr>`;
        }).join('') : '<tr><td colspan="4" class="empty">No hay productos por vencer</td></tr>';

        const badgeEl = $('#badge-alertas');
        const totalAlertas = stockBajo.length + porVencer.length;
        if (badgeEl) {
            if (totalAlertas > 0) {
                badgeEl.textContent = totalAlertas;
                badgeEl.style.display = 'inline-flex';
            } else {
                badgeEl.style.display = 'none';
            }
        }

        const criticas = porVencer.filter(p => p.estado === 'vencido').length + stockBajo.filter(p => p.stock === 0).length;
        if (criticas > 0 && !sessionStorage.getItem('notif_v2')) {
            const banner = document.createElement('div');
            banner.className = 'notif-banner';
            var txtCrit = criticas + ' alerta' + (criticas > 1 ? 's' : '') + ' critica' + (criticas > 1 ? 's' : '');
            banner.innerHTML = '<span class="notif-banner-icon">!</span>' +
                '<div class="notif-banner-text">' +
                '<strong>' + txtCrit + '</strong>' +
                '<span>Hay productos vencidos o sin stock. Revisa las alertas del inventario.</span>' +
                '</div>' +
                '<button class="notif-banner-close" onclick="this.parentElement.remove(); sessionStorage.setItem(\'notif_v2\',\'1\')">&#10005;</button>';
            document.body.prepend(banner);
            setTimeout(() => { if (banner.parentElement) banner.remove(); sessionStorage.setItem('notif_v2', '1'); }, 15000);
        }

        try {
            const g = await request(API + '/dashboard/graficos');
            graficoVentasGastos(g.meses);
            graficoUtilidad(g.meses);
            graficoHBar('#graf-top-productos', g.top_productos, 'cantidad', 'unid', '#FCC302');
            graficoHBar('#graf-top-repartos', g.top_repartos, 'total', 'Bs ', '#CF141D');
        } catch (e) { /* gráficos opcionales */ }

        graficoHBar('#graf-top-entradas', d.top_entrada, 'total', ' ', '#2e7d32');
        graficoHBar('#graf-top-salidas', d.top_salida, 'total', ' ', '#CF141D');
    } catch (e) {
        toast(e.message, 'err');
    }
}

function fmtCompacto(n) {
    if (Math.abs(n) >= 1000) return (n / 1000).toFixed(1).replace('.0', '') + 'k';
    return String(Math.round(n));
}

function svgBarras(data, getValor, color, opt = {}) {
    const w = opt.w || Math.max(560, data.length * 68), h = opt.h || 200;
    const padB = 30, padL = 46, padT = 12;
    const max = Math.max(...data.map((d) => getValor(d)), 1);
    const innerW = w - padL - 8, innerH = h - padB - padT;
    const n = data.length || 1;
    const slot = innerW / n;
    const barW = Math.min(34, slot * 0.55);
    let s = `<svg style="width:${w}px;max-width:100%" viewBox="0 0 ${w} ${h}">`;
    for (let i = 0; i <= 4; i++) {
        const y = padT + innerH - (innerH * i / 4);
        s += `<line x1="${padL}" y1="${y}" x2="${w - 8}" y2="${y}" stroke="#eee" stroke-width="1"/>`;
        s += `<text x="${padL - 6}" y="${y + 4}" text-anchor="end" class="chart-axis">${fmtCompacto(max * i / 4)}</text>`;
    }
    data.forEach((d, i) => {
        const cx = padL + slot * i + slot / 2;
        const vh = (getValor(d) / max) * innerH;
        const x = cx - barW / 2;
        s += `<rect x="${x}" y="${padT + innerH - vh}" width="${barW}" height="${Math.max(vh, 1)}" rx="3" fill="${color}"><title>${getValor(d)}</title></rect>`;
        s += `<text x="${cx}" y="${h - 8}" text-anchor="middle" class="chart-axis">${d.mes}</text>`;
    });
    s += '</svg>';
    return s;
}

function graficoVentasGastos(meses) {
    const max = Math.max(...meses.flatMap((m) => [m.ventas, m.gastos]), 1);
    const w = Math.max(560, meses.length * 68), h = 200, padB = 30, padL = 46, padT = 12;
    const innerW = w - padL - 8, innerH = h - padB - padT;
    const n = meses.length || 1;
    const slot = innerW / n;
    const barW = Math.min(16, slot / 3.2);
    let s = `<svg style="width:${w}px;max-width:100%" viewBox="0 0 ${w} ${h}">`;
    for (let i = 0; i <= 4; i++) {
        const y = padT + innerH - (innerH * i / 4);
        s += `<line x1="${padL}" y1="${y}" x2="${w - 8}" y2="${y}" stroke="#eee" stroke-width="1"/>`;
        s += `<text x="${padL - 6}" y="${y + 4}" text-anchor="end" class="chart-axis">${fmtCompacto(max * i / 4)}</text>`;
    }
    meses.forEach((m, i) => {
        const cx = padL + slot * i + slot / 2;
        const vh = (m.ventas / max) * innerH;
        const gh = (m.gastos / max) * innerH;
        const yv = padT + innerH - vh, yg = padT + innerH - gh;
        s += `<rect x="${cx - barW - 1.5}" y="${yv}" width="${barW}" height="${Math.max(vh, 1)}" rx="3" fill="#FCC302"><title>Ventas: Bs ${m.ventas}</title></rect>`;
        s += `<rect x="${cx + 1.5}" y="${yg}" width="${barW}" height="${Math.max(gh, 1)}" rx="3" fill="#CF141D"><title>Gastos: Bs ${m.gastos}</title></rect>`;
        s += `<text x="${cx}" y="${h - 8}" text-anchor="middle" class="chart-axis">${m.mes}</text>`;
    });
    s += '</svg>';
    $('#graf-ventas-gastos').innerHTML = s;
}

function graficoUtilidad(meses) {
    $('#graf-utilidad').innerHTML = svgBarras(meses, (m) => m.utilidad, '#2e7d32');
}

function graficoHBar(selector, items, campo, pref, color) {
    const cont = $(selector);
    if (!items || !items.length) {
        cont.innerHTML = '<p class="empty">Sin datos todavía</p>';
        return;
    }
    const max = Math.max(...items.map((i) => Number(i[campo]) || 0), 1);
    cont.innerHTML = items.map((i) => {
        const v = Number(i[campo]) || 0;
        return `<div class="hbar-row">
            <span class="hbar-label" title="${i.nombre}">${i.nombre}</span>
            <div class="hbar-track"><div class="hbar-fill" style="width:${(v / max) * 100}%;background:${color}"></div></div>
            <span class="hbar-value">${pref}${fmtCompacto(v)}</span>
        </div>`;
    }).join('');
}

// ---------------- Productos ----------------
function esAdmin() { return window.ROL === 'admin' || window.ROL === 'superadmin'; }
function esAlmacenPpal() { return window.ROL === 'encargado' && !!window.SUCURSAL_PRINCIPAL; }
// Admin/superadmin y encargados de almacén principal actúan como "mano derecha" del admin
function esCentral() { return esAdmin() || esAlmacenPpal(); }

let _prodSuc = '';
async function pintarProdScope() {
    const row = $('#prod-scope-tabs');
    if (!row) return;
    if (!catalogos || !catalogos.sucursales) await loadCatalogos();
    const botones = [{ v: '', lbl: 'Todas' }]
        .concat((catalogos.sucursales || []).map((s) => ({ v: String(s.id), lbl: s.nombre })));
    const ctr = {};
    for (const b of botones) {
        try {
            const r = await request(API + '/productos?por_pagina=1' + (b.v ? '&sucursal=' + b.v : ''));
            ctr[b.v] = r.total != null ? r.total : (r.data || r).length || 0;
        } catch (_) { ctr[b.v] = 0; }
    }
    row.innerHTML = botones.map((f) =>
        `<button class="btn hist-tab ${_prodSuc === f.v ? 'active' : ''}" data-prod-suc="${f.v}">${f.lbl} · ${ctr[f.v]}</button>`).join('');
    row.querySelectorAll('[data-prod-suc]').forEach((b) => b.addEventListener('click', () => {
        _prodSuc = b.dataset.prodSuc;
        pintarProdScope();
        loadProductos();
    }));
}

async function loadProductos() {
    try {
        const filtro = ($('#prod-filtro') || {}).value?.trim() || '';
        const categoria = ($('#prod-categoria') || {}).value || '';
        const proveedor = ($('#prod-filtro-proveedor') || {}).value || '';
        const estado = ($('#prod-estado') || {}).value || '';
        const qs = new URLSearchParams();
        if (filtro) qs.set('filtro', filtro);
        if (categoria) qs.set('categoria', categoria);
        if (proveedor) qs.set('proveedor', proveedor);
        if (estado) qs.set('estado', estado);
        if (_prodSuc !== '') qs.set('sucursal', _prodSuc);
        qs.set('por_pagina', 1000);
        const resp = await request(API + '/productos?' + qs.toString());
        const prods = resp.data || resp;
        $('#prod-scope-info').textContent = 'Mostrando ' + (resp.total != null ? resp.total : prods.length) + ' producto(s)';

        if (!catalogos || !catalogos.sucursales) await loadCatalogos();
        const esGestion = esCentral();
        const container = $('#productos-por-categoria');
        container.innerHTML = '';

        const grupos = (catalogos.sucursales || []).map((s) => ({ id: s.id, nombre: s.nombre }));
        const cats = [{ id: 0, nombre: 'Sin categoría' }]
            .concat((catalogos.categorias || []).map((c) => ({ id: c.id, nombre: c.nombre })));
        let visibles = _prodSuc === '' ? grupos : grupos.filter((g) => String(g.id) === _prodSuc);
        let hay = false;

        visibles.forEach((g) => {
            const items = prods.filter((p) => p.sucursal_id === g.id);
            if (!items.length) return;
            hay = true;
            renderSucursal(g, items);
        });

        if (!hay) {
            container.innerHTML = '<div class="empty">No hay productos</div>';
        }

        function renderSucursal(g, items) {
            const bloques = cats
                .map((c) => ({
                    c,
                    items: items.filter((p) =>
                        c.id === 0
                            ? !(catalogos.categorias || []).some((x) => x.id === p.categoria_id)
                            : p.categoria_id === c.id)
                }))
                .filter((b) => b.items.length);

            const filas = (it) => it.map(p => {
                let stockColor, stockIcon;
                if (p.stock === 0) { stockColor = '#dc2626'; stockIcon = '🔴'; }
                else if (p.stock <= (p.stock_minimo || 10)) { stockColor = '#d97706'; stockIcon = '🟡'; }
                else { stockColor = '#16a34a'; stockIcon = '🟢'; }
                const esPropio = window.ROL === 'encargado' && p.sucursal_id === window.SUCURSAL_ID;
                const puedeEditar = esGestion || esPropio;
                const acciones = estado === 'inactivos'
                    ? (esAdmin() ? `<button class="btn btn-icon" data-restaurar-prod="${p.id}" title="Restaurar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg></button>` : '')
                    : `<button class="btn btn-icon" data-hist-prod="${p.id}" title="Historial"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></button>${
                        puedeEditar
                            ? `<button class="btn btn-icon" data-edit-prod="${p.id}" title="Editar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg></button>
                        <button class="btn btn-icon btn-danger" data-del-prod="${p.id}" title="Eliminar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>`
                            : ''}`;
                return `<tr>
                    <td><strong>${nomProd(p)}</strong><br><small style="color:var(--muted)">${esc(p.codigo || '')}</small></td>
                    <td>${esc(p.almacen_nombre || '—')}</td>
                    <td style="color:${stockColor};font-weight:700">${stockIcon} ${p.stock} ${p.unidad}</td>
                    <td>${p.stock_minimo} ${p.unidad}</td>
                    <td>${p.unidad}</td>
                    <td>Bs ${fmtNum(p.costo_promedio)}</td>
                    <td>Bs ${fmtNum(p.precio_venta)}</td>
                    <td>${p.proveedor_nombre || '—'}</td>
                    <td>${p.vencimiento ? fmtDate(p.vencimiento) : '—'}</td>
                    <td>${acciones}
                    </td>
                </tr>`;
            }).join('');

            container.innerHTML += `
                <div class="categoria-card">
                    <div class="categoria-header">
                        <h3>${esc(g.nombre)}</h3>
                        <span class="badge badge-info">${items.length} producto(s)</span>
                    </div>
                    ${bloques.length
                        ? bloques.map((b) => `
                            <div class="subcat-titulo">${esc(b.c.nombre)} · ${b.items.length}</div>
                            <div class="categoria-table-wrap">
                                <table class="data-table">
                                    <thead>
                                        <tr>
                                            <th>Producto</th><th>Almacén</th><th>Stock</th>
                                            <th>Mínimo</th><th>Unidad</th><th>Costo (Bs)</th><th>Precio (Bs)</th>
                                            <th>Proveedor</th><th>Vence</th><th></th>
                                        </tr>
                                    </thead>
                                    <tbody>${filas(b.items)}</tbody>
                                </table>
                            </div>`).join('')
                        : '<div class="empty">Sin productos registrados</div>'}
                </div>`;
        }

        $$('[data-edit-prod]').forEach((b) => b.addEventListener('click', () => openProductoModal(Number(b.dataset.editProd), prods)));
        $$('[data-del-prod]').forEach((b) => b.addEventListener('click', () => delProducto(b.dataset.delProd)));
        $$('[data-hist-prod]').forEach((b) => b.addEventListener('click', () => verHistorialProducto(Number(b.dataset.histProd))));
        $$('[data-restaurar-prod]').forEach((b) => b.addEventListener('click', async () => {
            if (!confirm('¿Restaurar este producto?')) return;
            try {
                await request(API + '/productos/' + b.dataset.restaurarProd + '/restaurar', { method: 'POST' });
                toast('Producto restaurado');
                loadProductos();
            } catch (e) { toast(e.message, 'err'); }
        }));
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#prod-filtro').addEventListener('input', debounce(loadProductos, 300));
$('#prod-categoria').addEventListener('change', loadProductos);
on('#prod-filtro-proveedor', 'change', loadProductos);
on('#prod-estado', 'change', loadProductos);
$('#btn-nuevo-producto').addEventListener('click', () => openProductoModal());
$('#prod-escaneo').addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const codigo = $('#prod-escaneo').value.trim();
    $('#prod-escaneo').value = '';
    if (!codigo) return;
    try {
        const p = await buscarProductoPorCodigo(codigo);
        toast('Producto encontrado: ' + p.nombre + ' (stock: ' + p.stock + ')', 'ok');
        $('#prod-filtro').value = p.nombre;
        loadProductos();
    } catch (err) {
        toast('Código no registrado. Abriendo formulario para crear producto...', 'err');
        abrirNuevoProductoConCodigo(codigo);
    }
});
$('#prod-escaneo').addEventListener('click', () => $('#prod-escaneo').select());

async function verHistorialProducto(id) {
    try {
        const data = await request(API + '/productos/' + id + '/historial');
        const p = data.producto;
        $('#hist-prod-title').textContent = 'Historial: ' + p.nombre;
        $('#hist-prod-codigo').textContent = p.codigo || '—';
        $('#hist-prod-unidad').textContent = p.unidad;
        $('#hist-prod-stock').textContent = data.stock + ' ' + p.unidad;
        $('#hist-prod-costo').textContent = 'Bs ' + fmtNum(p.costo_promedio);
        $('#hist-prod-movs').innerHTML = data.movimientos.map(m => `
            <tr>
                <td>${fmtDate(m.fecha)}</td>
                <td><span class="badge badge-${m.tipo === 'entrada' ? 'success' : 'warning'}">${m.tipo === 'entrada' ? 'Entrada' : 'Salida'}</span></td>
                <td>${m.tipo === 'entrada' ? '+' : '-'}${m.cantidad}</td>
                <td>Bs ${fmtNum(m.precio_unitario)}</td>
                <td>${m.nota || '—'}</td>
                <td>${m.usuario || '—'}</td>
            </tr>
        `).join('') || '<tr><td colspan="6" class="empty">Sin movimientos</td></tr>';
        $('#modal-hist-producto').classList.add('open');
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#btn-exportar-productos').addEventListener('click', () => {
    const sel = $('#exportar-sucursal');
    if (sel) sel.value = _prodSuc;
    const selCat = $('#exportar-categoria');
    if (selCat) selCat.value = ($('#prod-categoria') || {}).value || '';
    $('#modal-exportar-prod').classList.add('open');
});
$('#btn-confirmar-exportar').addEventListener('click', () => {
    $('#modal-exportar-prod').classList.remove('open');
    exportarProductos();
});

$('#btn-importar-prod').addEventListener('click', () => {
    $('#importar-resultado').innerHTML = '';
    $('#importar-nombre-archivo').textContent = '';
    $('#importar-archivo').value = '';
    $('#btn-ejecutar-importar').disabled = true;
    $('#modal-importar-prod').classList.add('open');
});
$('#btn-seleccionar-archivo').addEventListener('click', () => $('#importar-archivo').click());
$('#importar-archivo').addEventListener('change', () => {
    const f = $('#importar-archivo').files[0];
    if (f) {
        $('#importar-nombre-archivo').textContent = f.name;
        $('#btn-ejecutar-importar').disabled = false;
    }
});
$('#btn-ejecutar-importar').addEventListener('click', async () => {
    const archivo = $('#importar-archivo').files[0];
    if (!archivo) return;
    const fd = new FormData();
    fd.append('archivo', archivo);
    const impSuc = $('#importar-sucursal');
    if (impSuc) fd.append('sucursal_id', impSuc.value);
    const impCat = $('#importar-categoria');
    if (impCat && impCat.value) fd.append('categoria_id', impCat.value);
    try {
        const res = await fetch(API + '/productos/importar', {
            method: 'POST', body: fd
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.message || data.error || 'Error al importar');
        let html = '<p style="color:var(--success);font-weight:600;">' + data.message + '</p>';
        if (data.errores && data.errores.length) {
            html += '<ul style="color:var(--danger);font-size:13px;margin-top:6px;">';
            data.errores.forEach(e => html += '<li>' + esc(e) + '</li>');
            html += '</ul>';
        }
        $('#importar-resultado').innerHTML = html;
        $('#btn-ejecutar-importar').disabled = true;
        loadProductos();
        loadDashboard();
    } catch (e) { toast(e.message, 'err'); }
});

function exportarProductos() {
    const filtro = ($('#prod-filtro') || {}).value?.trim() || '';
    const proveedor = ($('#prod-filtro-proveedor') || {}).value || '';
    const estado = ($('#prod-estado') || {}).value || '';
    const suc = ($('#exportar-sucursal') || {}).value;
    const categoria = ($('#exportar-categoria') || {}).value || '';
    const qs = new URLSearchParams();
    if (filtro) qs.set('filtro', filtro);
    if (categoria) qs.set('categoria', categoria);
    if (proveedor) qs.set('proveedor', proveedor);
    if (estado) qs.set('estado', estado);
    if (suc !== undefined && suc !== '') qs.set('sucursal', suc);
    window.location.href = API + '/exportar/productos?' + qs.toString();
}

async function openProductoModal(id, lista) {
    await loadCatalogos();
    const form = $('#form-producto');
    form.reset();
    $('#prod-id').value = '';
    $('#modal-producto-title').textContent = 'Nuevo producto';
    $('#campo-stock-inicial').style.display = 'flex';
    $('#campo-stock-actual').style.display = 'none';
    $('#prod-stock-hint').style.display = 'none';
    const esGestionDlg = esCentral();
    const lblSuc = $('#prod-sucursal-label');
    if (lblSuc) lblSuc.style.display = esGestionDlg ? 'block' : 'none';
    $('#prod-sucursal').value = esGestionDlg ? (window.SUCURSAL_ID || '') : '';
    if (!id && esGestionDlg) {
        const match = (catalogos.almacenes || []).find((a) => a.sucursal_id === +($('#prod-sucursal').value || 0));
        if (match) $('#prod-almacen').value = match.id;
    }

    if (id) {
        const p = lista.find((x) => x.id === id);
        $('#modal-producto-title').textContent = 'Editar producto';
        $('#prod-id').value = p.id;
        $('#prod-codigo').value = p.codigo || '';
        $('#prod-nombre').value = p.nombre;
        $('#prod-marca').value = p.marca || '';
        $('#prod-categoria-form').value = p.categoria_id || '';
        $('#prod-almacen').value = p.almacen_id || '';
        $('#prod-unidad').value = p.unidad || 'unidad';
        $('#prod-minimo').value = p.stock_minimo;
        $('#prod-costo').value = p.costo_promedio;
        $('#prod-precio-venta').value = p.precio_venta;
        $('#prod-vencimiento').value = p.vencimiento || '';
        $('#prod-proveedor').value = p.proveedor_id || '';
        if (esGestionDlg) $('#prod-sucursal').value = p.sucursal_id || '';
        if (esGestionDlg && p.sucursal_id && !p.almacen_id) {
            const match = (catalogos.almacenes || []).find((a) => a.sucursal_id === p.sucursal_id);
            if (match) $('#prod-almacen').value = match.id;
        }
        $('#campo-stock-inicial').style.display = 'none';
        $('#campo-stock-actual').style.display = 'flex';
        $('#prod-stock-hint').style.display = 'block';
        $('#prod-stock-actual').value = p.stock;
    }
    $('#modal-producto').classList.add('open');
}

$('#form-producto').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = $('#prod-id').value;
    if (esCentral() && !$('#prod-sucursal').value) {
        toast('Selecciona una sucursal para el producto', 'err');
        return;
    }
    const body = {
        codigo: $('#prod-codigo').value.trim() || null,
        nombre: $('#prod-nombre').value,
        marca: $('#prod-marca').value.trim() || null,
        categoria_id: +$('#prod-categoria-form').value || null,
        almacen_id: +$('#prod-almacen').value || null,
        unidad: $('#prod-unidad').value,
        stock_minimo: +$('#prod-minimo').value || 0,
        costo_promedio: +$('#prod-costo').value || 0,
        precio_venta: +$('#prod-precio-venta').value || 0,
        vencimiento: $('#prod-vencimiento').value || null,
        proveedor_id: +$('#prod-proveedor').value || null,
        stock_inicial: +$('#prod-stock-inicial').value || 0,
    };
    if (esCentral()) {
        body.sucursal_id = +$('#prod-sucursal').value || null;
    }
    if (id) body.stock = +$('#prod-stock-actual').value || 0;
    try {
        let resp = null;
        if (id) {
            resp = await request(API + '/productos/' + id, { method: 'PUT', body: JSON.stringify(body) });
        } else {
            resp = await request(API + '/productos', { method: 'POST', body: JSON.stringify(body) });
        }
        toast(id ? 'Producto actualizado' : 'Producto creado');
        if (resp && resp.aviso) setTimeout(() => toast('⚠ ' + resp.aviso, 'err'), 400);
        $('#modal-producto').classList.remove('open');
        loadProductos();
    } catch (err) {
        toast(err.message, 'err');
    }
});

async function delProducto(id) {
    if (!confirm('¿Eliminar este producto?')) return;
    try {
        await request(API + '/productos/' + id, { method: 'DELETE' });
        toast('Producto eliminado');
        loadProductos();
    } catch (e) {
        toast(e.message, 'err');
    }
}

// Al elegir sucursal en el modal de producto, preseleccionar su almacén
$('#prod-sucursal').addEventListener('change', () => {
    const sel = +$('#prod-sucursal').value;
    const alm = $('#prod-almacen');
    if (alm && sel) {
        const match = (catalogos.almacenes || []).find((a) => a.sucursal_id === sel);
        if (match) alm.value = match.id;
    }
});

// ---------------- Movimientos ----------------
let movProdsAll = [];

function popMovProductos(sid) {
    const sel = $('#mov-producto');
    if (!sel) return;
    const lista = sid ? movProdsAll.filter((p) => p.sucursal_id === sid) : movProdsAll;
    sel.innerHTML = lista.length
        ? '<option value="">Seleccione...</option>' +
            lista.map((p) => `<option value="${p.id}">${nomProd(p)} (${p.stock} ${p.unidad})</option>`).join('')
        : '<option value="">No hay productos en esta sucursal</option>';
}

async function loadMovimientos() {
    try {
        const resp = await request(API + '/productos?por_pagina=1000');
        movProdsAll = resp.data || resp;
        // Inicializar tabs histórico (solo admin/superadmin ven el selector de sucursales)
        const esGestion = (window.ROL === 'admin' || window.ROL === 'superadmin');
        const tabs = $('#hist-sucursal-tabs');
        const select = $('#hist-sucursal-select');
        if (tabs) {
            if (esGestion) {
                tabs.style.display = 'flex';
                // Poblar selector con todas las sucursales
                try {
                    const sucs = await request(API + '/sucursales');
                    select.innerHTML = '<option value="">Todas las sucursales</option>' +
                        sucs.map((s) => `<option value="${s.id}">${s.principal ? '★ ' : ''}${s.nombre}</option>`).join('');
                } catch (e) { /* sin sucursales */ }
                // Por defecto activa la vista "Almacén de Sucursales" sin filtro = se ven TODAS
                $('#btn-hist-mio').classList.remove('active');
                $('#btn-hist-sucursales').classList.add('active');
                select.style.display = 'inline-flex';
                select.value = '';
            } else {
                tabs.style.display = 'none';
                $('#btn-hist-mio').classList.add('active');
                $('#btn-hist-sucursales').classList.remove('active');
                select.style.display = 'none';
            }
        }
        // Selector de sucursal en "Registrar movimiento": admin/superadmin pueden elegir
        // cualquier sucursal; encargado registra siempre en la suya (oculto).
        const movAlm = $('#mov-almacen');
        const movAlmLabel = $('#mov-almacen-label');
        let sidMov = window.SUCURSAL_ID || null;
        if (movAlm && movAlmLabel) {
            if (window.ROL === 'admin' || window.ROL === 'superadmin') {
                movAlmLabel.style.display = 'block';
                try {
                    const sucs = await request(API + '/sucursales');
                    movAlm.innerHTML = sucs.map((s) => `<option value="${s.id}">${s.principal ? '★ ' : ''}${s.nombre}</option>`).join('');
                    movAlm.value = window.SUCURSAL_ID || (sucs.find((s) => s.principal) || {}).id || '';
                } catch (e) { /* sin sucursales */ }
                sidMov = +movAlm.value || window.SUCURSAL_ID || null;
            } else {
                movAlmLabel.style.display = 'none';
            }
        }
        popMovProductos(sidMov);
        await listarMovimientos();
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#mov-almacen').addEventListener('change', () => {
    popMovProductos(+$('#mov-almacen').value || null);
});

async function listarMovimientos() {
    const qs = new URLSearchParams();
    const filtro = ($('#mov-filtro') || {}).value?.trim() || '';
    const desde = ($('#mov-desde') || {}).value || '', hasta = ($('#mov-hasta') || {}).value || '';
    if (filtro) qs.set('filtro', filtro);
    if (desde) qs.set('desde', desde);
    if (hasta) qs.set('hasta', hasta);
    if ($('#mov-filtro-tipo').value) qs.set('tipo', $('#mov-filtro-tipo').value);
    // Filtro por sucursal: "Almacén de Sucursales" activo + sucursal elegida
    const tabSucursales = $('#btn-hist-sucursales');
    const select = $('#hist-sucursal-select');
    const enSucursales = tabSucursales && tabSucursales.classList.contains('active');
    if (enSucursales && select && select.value) {
        qs.set('sucursal_id', select.value);
    }
    qs.set('pagina', pagState['#movimientos-paginacion'] || 1);
    const resp = await request(API + '/movimientos?' + qs.toString());
    const movs = resp.data || resp;
    const total = resp.total || movs.length;
    const pagina = resp.pagina || 1;
    const porPagina = resp.por_pagina || 50;
    $('#movimientos-tbody').innerHTML = movs.map((m) => `
        <tr>
            <td>${fmtDate(m.fecha)}</td>
            <td>${esc(m.producto_nombre)}</td>
            <td><span class="badge badge-${m.tipo}">${m.tipo === 'entrada' ? 'Entrada' : 'Salida'}</span></td>
            <td>${m.cantidad} ${m.unidad}</td>
            <td>${fmtNum(m.precio_unitario)}</td>
            <td>${m.sucursal_nombre || '—'}</td>
            <td>${m.proveedor_nombre || '—'}</td>
            <td>${m.nota || '—'}</td>
            <td>${m.usuario || '—'}</td>
        </tr>`).join('') || '<tr><td colspan="8" class="empty">Sin movimientos</td></tr>';
    renderPagination('#movimientos-paginacion', total, pagina, porPagina, listarMovimientos);
}

// ---------------- Tabs histórico por sucursal (admin/superadmin) ----------------
$('#btn-hist-mio').addEventListener('click', () => {
    $('#btn-hist-mio').classList.add('active');
    $('#btn-hist-sucursales').classList.remove('active');
    $('#hist-sucursal-select').style.display = 'none';
    pagState['#movimientos-paginacion'] = 1;
    listarMovimientos();
});
$('#btn-hist-sucursales').addEventListener('click', () => {
    $('#btn-hist-sucursales').classList.add('active');
    $('#btn-hist-mio').classList.remove('active');
    $('#hist-sucursal-select').style.display = 'inline-flex';
    pagState['#movimientos-paginacion'] = 1;
});
$('#hist-sucursal-select').addEventListener('change', () => {
    pagState['#movimientos-paginacion'] = 1;
    listarMovimientos();
});

$('#form-movimiento').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {
        producto_id: +$('#mov-producto').value,
        tipo: $('#mov-tipo').value,
        cantidad: +$('#mov-cantidad').value,
        precio_unitario: +$('#mov-precio').value || 0,
        fecha: fechaISO($('#mov-fecha').value) || undefined,
        sucursal_id: +$('#mov-almacen').value || null,
        proveedor_id: +$('#mov-proveedor').value || null,
        nota: $('#mov-nota').value,
    };
    if (!body.producto_id) return toast('Seleccione un producto', 'err');
    try {
        await request(API + '/movimientos', { method: 'POST', body: JSON.stringify(body) });
        toast('Movimiento registrado');
        e.target.reset();
        listarMovimientos();
    } catch (err) {
        toast(err.message, 'err');
    }
});

$('#mov-tipo').addEventListener('change', () => {
    const lbl = $('#mov-proveedor-label');
    if (lbl) lbl.style.display = $('#mov-tipo').value === 'salida' ? 'none' : 'block';
});

$('#btn-filtrar-mov').addEventListener('click', () => { pagState['#movimientos-paginacion'] = 1; listarMovimientos(); });
on('#mov-filtro', 'input', debounce(() => { pagState['#movimientos-paginacion'] = 1; listarMovimientos(); }, 300));
$('#mov-filtro-tipo').addEventListener('change', listarMovimientos);
$('#btn-exportar-mov').addEventListener('click', () => {
    const tabSucursales = $('#btn-hist-sucursales');
    const select = $('#hist-sucursal-select');
    const enSucursales = tabSucursales && tabSucursales.classList.contains('active') && select && select.value;
    window.location.href = '/api/exportar/movimientos?desde=' + ($('#mov-desde') || {}).value + '&hasta=' + ($('#mov-hasta') || {}).value + '&tipo=' + ($('#mov-filtro-tipo') || {}).value + '&filtro=' + ($('#mov-filtro') || {}).value + (enSucursales ? '&sucursal_id=' + select.value : '');
});
vincularEscaneo('#mov-escaneo', '#mov-producto', '#mov-cantidad');
$('#mov-tipo').addEventListener('change', () => {
    const label = $('#mov-precio');
    $('#mov-tipo').value === 'salida'
        ? label.closest('label').style.display = 'none'
        : label.closest('label').style.display = 'flex';
});

// ---------------- Escaneo rápido ----------------
let qrItems = {};

function actQrItems() {
    const tbody = $('#qr-items-tbody');
    const keys = Object.keys(qrItems);
    if (!keys.length) {
        tbody.innerHTML = '';
        $('#qr-empty').style.display = 'block';
        return;
    }
    $('#qr-empty').style.display = 'none';
    tbody.innerHTML = keys.map((k) => {
        const it = qrItems[k];
        return `<tr>
            <td><strong>${esc(it.nombre)}</strong></td>
            <td>${esc(it.codigo || '—')}</td>
            <td>${esc(it.unidad)}</td>
            <td>
                <button class="btn btn-icon" onclick="qrCant('${k}', -1)">−</button>
                <strong style="margin:0 8px">${it.cantidad}</strong>
                <button class="btn btn-icon" onclick="qrCant('${k}', 1)">+</button>
            </td>
            <td><button class="btn btn-icon btn-danger" onclick="qrRemove('${k}')">✕</button></td>
        </tr>`;
    }).join('');
}

window.qrCant = (key, delta) => {
    if (!qrItems[key]) return;
    qrItems[key].cantidad = Math.max(1, qrItems[key].cantidad + delta);
    actQrItems();
};

window.qrRemove = (key) => {
    delete qrItems[key];
    actQrItems();
};

$('#btn-qr-limpiar').addEventListener('click', () => {
    qrItems = {};
    actQrItems();
});

$('#qr-escaneo').addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const codigo = $('#qr-escaneo').value.trim();
    $('#qr-escaneo').value = '';
    if (!codigo) return;
    try {
        const p = await request(API + '/productos/codigo?codigo=' + encodeURIComponent(codigo));
        if (p.sucursal_id && p.sucursal_id !== window.SUCURSAL_ID) {
            toast('Ese producto no pertenece a tu sucursal', 'err');
            return;
        }
        const key = String(p.id);
        if (qrItems[key]) {
            qrItems[key].cantidad += 1;
        } else {
            qrItems[key] = { producto_id: p.id, nombre: p.nombre, codigo: p.codigo, unidad: p.unidad,
                cantidad: 1, precio_unitario: p.costo_promedio || p.precio_venta || 0 };
        }
        actQrItems();
        toast(`${p.nombre} → ${qrItems[key].cantidad} ${p.unidad}`);
    } catch (err) {
        toast('Código no registrado: ' + codigo, 'err');
    }
});

$('#btn-qr-registrar').addEventListener('click', async () => {
    const keys = Object.keys(qrItems);
    if (!keys.length) return toast('No hay productos para registrar', 'err');
    const tipo = $('#qr-tipo').value;
    const proveedor_id = +($('#qr-proveedor').value) || null;
    const items = keys.map((k) => ({
        producto_id: qrItems[k].producto_id,
        cantidad: qrItems[k].cantidad,
        precio_unitario: qrItems[k].precio_unitario || 0,
    }));
    try {
        const res = await request(API + '/movimientos/lote', {
            method: 'POST',
            body: JSON.stringify({ tipo, items, proveedor_id }),
        });
        toast(res.message || 'Registrado');
        qrItems = {};
        actQrItems();
        listarMovimientos();
    } catch (err) {
        toast(err.message, 'err');
    }
});

$('#qr-escaneo').addEventListener('click', () => $('#qr-escaneo').select());

// ---------------- Proveedores ----------------
async function loadProveedores() {
    try {
        const esGestion = (window.ROL === 'admin' || window.ROL === 'superadmin');
        const tabs = $('#prov-tabs'), select = $('#prov-sucursal-select');
        if (tabs) {
            const btnSuc = $('#btn-prov-sucursales');
            if (esGestion) {
                tabs.style.display = 'flex';
                select.style.display = (btnSuc && btnSuc.classList.contains('active')) ? 'inline-flex' : 'none';
            } else {
                tabs.style.display = 'none';
            }
        }
        const qs = new URLSearchParams();
        const filtro = ($('#proveedor-filtro') || {}).value?.trim() || '';
        if (filtro) qs.set('filtro', filtro);
        if (esGestion && select && select.value) qs.set('sucursal_id', select.value);
        const provs = await request(API + '/proveedores?' + qs.toString());
        $('#proveedores-tbody').innerHTML = provs.map((p) => `
            <tr>
                <td><strong>${p.nombre}</strong></td>
                <td>${p.telefono || '—'}</td>
                <td>${p.email || '—'}</td>
                <td>${p.direccion || '—'}</td>
                <td>${p.sucursal_nombre || '—'}</td>
                <td>
                    <button class="btn btn-icon" data-edit-prov="${p.id}" title="Editar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg></button>
                    <button class="btn btn-icon btn-danger" data-del-prov="${p.id}" title="Eliminar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
                </td>
            </tr>`).join('') || '<tr><td colspan="6" class="empty">Sin proveedores</td></tr>';

        $$('[data-edit-prov]').forEach((b) => b.addEventListener('click', () => openProveedorModal(Number(b.dataset.editProv), provs)));
        $$('[data-del-prov]').forEach((b) => b.addEventListener('click', () => delProveedor(b.dataset.delProv)));
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#btn-prov-mio').addEventListener('click', () => {
    $('#btn-prov-mio').classList.add('active');
    $('#btn-prov-sucursales').classList.remove('active');
    $('#prov-sucursal-select').style.display = 'none';
    $('#prov-sucursal-select').value = '';
    loadProveedores();
});
$('#btn-prov-sucursales').addEventListener('click', () => {
    $('#btn-prov-sucursales').classList.add('active');
    $('#btn-prov-mio').classList.remove('active');
    $('#prov-sucursal-select').style.display = 'inline-flex';
    loadProveedores();
});
$('#prov-sucursal-select').addEventListener('change', loadProveedores);

$('#btn-nuevo-proveedor').addEventListener('click', () => openProveedorModal());
on('#proveedor-filtro', 'input', debounce(loadProveedores, 300));

function openProveedorModal(id, lista) {
    const form = $('#form-proveedor');
    form.reset();
    $('#prov-id').value = '';
    const esGestion = (window.ROL === 'admin' || window.ROL === 'superadmin');
    const lbl = $('#prov-sucursal-label');
    if (lbl) lbl.style.display = esGestion ? 'block' : 'none';
    if (esGestion) {
        $('#prov-sucursal').value = window.SUCURSAL_ID || '';
    }
    if (id) {
        const p = lista.find((x) => x.id === id);
        $('#prov-id').value = p.id;
        $('#prov-nombre').value = p.nombre;
        $('#prov-telefono').value = p.telefono;
        $('#prov-email').value = p.email;
        $('#prov-direccion').value = p.direccion;
        if (esGestion) $('#prov-sucursal').value = p.sucursal_id || '';
    }
    $('#modal-proveedor').classList.add('open');
}

$('#form-proveedor').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = $('#prov-id').value;
    const body = {
        nombre: $('#prov-nombre').value,
        telefono: $('#prov-telefono').value,
        email: $('#prov-email').value,
        direccion: $('#prov-direccion').value,
    };
    if (window.ROL === 'admin' || window.ROL === 'superadmin') {
        body.sucursal_id = +$('#prov-sucursal').value || null;
    }
    try {
        if (id) {
            await request(API + '/proveedores/' + id, { method: 'PUT', body: JSON.stringify(body) });
        } else {
            await request(API + '/proveedores', { method: 'POST', body: JSON.stringify(body) });
        }
        toast(id ? 'Proveedor actualizado' : 'Proveedor creado');
        $('#modal-proveedor').classList.remove('open');
        loadProveedores();
    } catch (err) {
        toast(err.message, 'err');
    }
});

async function delProveedor(id) {
    if (!confirm('¿Eliminar este proveedor?')) return;
    try {
        await request(API + '/proveedores/' + id, { method: 'DELETE' });
        toast('Proveedor eliminado');
        loadProveedores();
    } catch (e) {
        toast(e.message, 'err');
    }
}

// ---------------- Gastos ----------------
async function loadGastos() {
    try {
        const esGestion = (window.ROL === 'admin' || window.ROL === 'superadmin');
        const tabs = $('#gasto-tabs'), select = $('#gasto-sucursal-select');
        if (tabs) {
            const btnSuc = $('#btn-gasto-sucursales');
            if (esGestion) {
                tabs.style.display = 'flex';
                select.style.display = (btnSuc && btnSuc.classList.contains('active')) ? 'inline-flex' : 'none';
            } else {
                tabs.style.display = 'none';
            }
        }
        const qs = new URLSearchParams();
        const filtro = ($('#gasto-filtro') || {}).value?.trim() || '';
        if (filtro) qs.set('filtro', filtro);
        if (($('#gasto-desde') || {}).value) qs.set('desde', $('#gasto-desde').value);
        if ($('#gasto-hasta').value) qs.set('hasta', $('#gasto-hasta').value);
        if (esGestion && select && select.value) qs.set('sucursal_id', select.value);
        qs.set('pagina', pagState['#gastos-paginacion'] || 1);
        const resp = await request(API + '/gastos?' + qs.toString());
        const gastos = resp.data || resp;
        const total = resp.total || gastos.length;
        const pagina = resp.pagina || 1;
        const porPagina = resp.por_pagina || 50;
        $('#gastos-tbody').innerHTML = gastos.map((g) => `
            <tr>
                <td>${fmtDate(g.fecha)}</td>
                <td>${esc(g.categoria)}</td>
                <td>${esc(g.descripcion) || '—'}</td>
                <td>${esc(g.proveedor_nombre) || '—'}</td>
                <td>${g.sucursal_nombre || '—'}</td>
                <td><strong>Bs ${fmtNum(g.monto)}</strong></td>
                <td>
                    <button class="btn btn-icon" data-edit-gasto="${g.id}" title="Editar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
                    <button class="btn btn-icon btn-danger" data-del-gasto="${g.id}" title="Eliminar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
                </td>
            </tr>`).join('') || '<tr><td colspan="7" class="empty">Sin gastos registrados</td></tr>';
        $$('[data-del-gasto]').forEach((b) => b.addEventListener('click', () => delGasto(b.dataset.delGasto)));
        $$('[data-edit-gasto]').forEach((b) => b.addEventListener('click', () => {
            const g = gastos.find((x) => x.id === Number(b.dataset.editGasto));
            if (g) editGasto(g);
        }));
        renderPagination('#gastos-paginacion', total, pagina, porPagina, loadGastos);
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#btn-gasto-mio').addEventListener('click', () => {
    $('#btn-gasto-mio').classList.add('active');
    $('#btn-gasto-sucursales').classList.remove('active');
    $('#gasto-sucursal-select').style.display = 'none';
    $('#gasto-sucursal-select').value = '';
    pagState['#gastos-paginacion'] = 1;
    loadGastos();
});
$('#btn-gasto-sucursales').addEventListener('click', () => {
    $('#btn-gasto-sucursales').classList.add('active');
    $('#btn-gasto-mio').classList.remove('active');
    $('#gasto-sucursal-select').style.display = 'inline-flex';
    pagState['#gastos-paginacion'] = 1;
    loadGastos();
});
$('#gasto-sucursal-select').addEventListener('change', () => {
    pagState['#gastos-paginacion'] = 1;
    loadGastos();
});

$('#form-gasto').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {
        categoria: $('#gasto-categoria').value,
        descripcion: $('#gasto-descripcion').value,
        monto: +$('#gasto-monto').value,
        fecha: fechaISO($('#gasto-fecha').value) || undefined,
        proveedor_id: +$('#gasto-proveedor').value || null,
    };
    try {
        await request(API + '/gastos', { method: 'POST', body: JSON.stringify(body) });
        toast('Gasto registrado');
        e.target.reset();
        loadGastos();
    } catch (err) {
        toast(err.message, 'err');
    }
});

$('#btn-filtrar-gastos').addEventListener('click', () => { pagState['#gastos-paginacion'] = 1; loadGastos(); });
on('#gasto-filtro', 'input', debounce(() => { pagState['#gastos-paginacion'] = 1; loadGastos(); }, 300));
$('#btn-exportar-gastos').addEventListener('click', () => {
    const select = $('#gasto-sucursal-select');
    const enSucursales = select && select.value;
    window.location.href = '/api/exportar/gastos?desde=' + ($('#gasto-desde') || {}).value + '&hasta=' + ($('#gasto-hasta') || {}).value + '&filtro=' + ($('#gasto-filtro') || {}).value + (enSucursales ? '&sucursal_id=' + select.value : '');
});

async function delGasto(id) {
    if (!confirm('¿Eliminar este gasto?')) return;
    try {
        await request(API + '/gastos/' + id, { method: 'DELETE' });
        toast('Gasto eliminado');
        loadGastos();
    } catch (e) {
        toast(e.message, 'err');
    }
}

// Editar gasto
$$('[data-edit-gasto]').forEach((b) => b.addEventListener('click', () => {}));
function editGasto(g) {
    $('#edit-gasto-id').value = g.id;
    $('#edit-gasto-categoria').value = g.categoria;
    $('#edit-gasto-descripcion').value = g.descripcion || '';
    $('#edit-gasto-monto').value = g.monto;
    const dt = g.fecha ? g.fecha.replace(' ', 'T').substring(0, 16) : '';
    $('#edit-gasto-fecha').value = dt;
    $('#edit-gasto-proveedor').innerHTML = '<option value="">Sin proveedor</option>' +
        catalogos.proveedores.map((p) => '<option value="' + p.id + '">' + esc(p.nombre) + '</option>').join('');
    $('#edit-gasto-proveedor').value = g.proveedor_id || '';
    $('#modal-editar-gasto').classList.add('open');
}
$('#form-editar-gasto').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        await request(API + '/gastos/' + $('#edit-gasto-id').value, {
            method: 'PUT',
            body: JSON.stringify({
                categoria: $('#edit-gasto-categoria').value,
                descripcion: $('#edit-gasto-descripcion').value,
                monto: +$('#edit-gasto-monto').value,
                fecha: fechaISO($('#edit-gasto-fecha').value) || undefined,
                proveedor_id: +$('#edit-gasto-proveedor').value || null,
            }),
        });
        toast('Gasto actualizado');
        $('#modal-editar-gasto').classList.remove('open');
        loadGastos();
    } catch (err) {
        toast(err.message, 'err');
    }
});

// ---------------- Almacenes ----------------
async function loadAlmacenes() {
    try {
        const almacenes = await request(API + '/almacenes');
        $('#almacenes-tbody').innerHTML = almacenes.map((a) => `
            <tr>
                <td>${esc(a.nombre)}</td>
                <td>${esc(a.ubicacion) || '—'}</td>
                <td>
                    <button class="btn btn-icon" data-edit-alm="${a.id}" title="Editar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
                    <button class="btn btn-icon btn-danger" data-del-alm="${a.id}" title="Eliminar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
                </td>
            </tr>`).join('') || '<tr><td colspan="3" class="empty">Sin almacenes</td></tr>';
        $$('[data-edit-alm]').forEach((b) => b.addEventListener('click', () => {
            const a = almacenes.find((x) => x.id === Number(b.dataset.editAlm));
            if (a) {
                $('#alm-id').value = a.id;
                $('#alm-nombre').value = a.nombre;
                $('#alm-ubicacion').value = a.ubicacion || '';
                $('#modal-almacen-title').textContent = 'Editar almacén';
                $('#modal-almacen').classList.add('open');
            }
        }));
        $$('[data-del-alm]').forEach((b) => b.addEventListener('click', async () => {
            if (!confirm('¿Eliminar este almacén?')) return;
            try {
                await request(API + '/almacenes/' + b.dataset.delAlm, { method: 'DELETE' });
                toast('Almacén eliminado');
                loadAlmacenes();
            } catch (e) { toast(e.message, 'err'); }
        }));
    } catch (e) { toast(e.message, 'err'); }
}
$('#btn-nuevo-almacen').addEventListener('click', () => {
    $('#alm-id').value = '';
    $('#form-almacen').reset();
    $('#modal-almacen-title').textContent = 'Nuevo almacén';
    $('#modal-almacen').classList.add('open');
});
$('#form-almacen').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = $('#alm-id').value;
    const body = { nombre: $('#alm-nombre').value, ubicacion: $('#alm-ubicacion').value };
    try {
        if (id) {
            await request(API + '/almacenes/' + id, { method: 'PUT', body: JSON.stringify(body) });
        } else {
            await request(API + '/almacenes', { method: 'POST', body: JSON.stringify(body) });
        }
        toast(id ? 'Almacén actualizado' : 'Almacén creado');
        $('#modal-almacen').classList.remove('open');
        loadAlmacenes();
        loadCatalogos();
    } catch (err) { toast(err.message, 'err'); }
});

// ---------------- Categorías ----------------
async function loadCategorias() {
    try {
        const cats = await request(API + '/categorias');
        $('#categorias-tbody').innerHTML = cats.map((c) => `
            <tr>
                <td>${esc(c.nombre)}</td>
                <td>
                    <button class="btn btn-icon" data-edit-cat="${c.id}" title="Editar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
                    <button class="btn btn-icon btn-danger" data-del-cat="${c.id}" title="Eliminar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
                </td>
            </tr>`).join('') || '<tr><td colspan="2" class="empty">Sin categorías</td></tr>';
        $$('[data-edit-cat]').forEach((b) => b.addEventListener('click', () => {
            const c = cats.find((x) => x.id === Number(b.dataset.editCat));
            if (c) {
                $('#cat-id').value = c.id;
                $('#cat-nombre').value = c.nombre;
                $('#modal-categoria-title').textContent = 'Editar categoría';
                $('#modal-categoria').classList.add('open');
            }
        }));
        $$('[data-del-cat]').forEach((b) => b.addEventListener('click', async () => {
            if (!confirm('¿Eliminar esta categoría?')) return;
            try {
                await request(API + '/categorias/' + b.dataset.delCat, { method: 'DELETE' });
                toast('Categoría eliminada');
                loadCategorias();
            } catch (e) { toast(e.message, 'err'); }
        }));
    } catch (e) { toast(e.message, 'err'); }
}
$('#btn-nueva-categoria').addEventListener('click', () => {
    $('#cat-id').value = '';
    $('#form-categoria').reset();
    $('#modal-categoria-title').textContent = 'Nueva categoría';
    $('#modal-categoria').classList.add('open');
});
$('#form-categoria').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = $('#cat-id').value;
    const body = { nombre: $('#cat-nombre').value };
    try {
        if (id) {
            await request(API + '/categorias/' + id, { method: 'PUT', body: JSON.stringify(body) });
        } else {
            await request(API + '/categorias', { method: 'POST', body: JSON.stringify(body) });
        }
        toast(id ? 'Categoría actualizada' : 'Categoría creada');
        $('#modal-categoria').classList.remove('open');
        loadCategorias();
        loadCatalogos();
    } catch (err) { toast(err.message, 'err'); }
});

// ---------------- Reportes ----------------
async function loadReportes() {
    try {
        const qs = new URLSearchParams();
        if ($('#rep-desde').value) qs.set('desde', $('#rep-desde').value);
        if ($('#rep-hasta').value) qs.set('hasta', $('#rep-hasta').value);
        const consumo = await request(API + '/reportes/consumo?' + qs.toString());
        $('#rep-consumo').innerHTML = consumo.map((c) => `
            <tr>
                <td>${c.nombre}</td>
                <td><span class="badge badge-${c.tipo}">${c.tipo === 'entrada' ? 'Entrada' : 'Salida'}</span></td>
                <td>${c.cantidad} ${c.unidad}</td>
                <td>Bs ${fmtNum(c.total)}</td>
            </tr>`).join('') || '<tr><td colspan="4" class="empty">Sin datos</td></tr>';

        const valorizacion = await request(API + '/reportes/valorizacion');
        $('#rep-valorizacion').innerHTML = valorizacion.map((v) => `
            <tr>
                <td>${v.nombre}</td><td>${v.stock} ${v.unidad}</td>
                <td>Bs ${fmtNum(v.costo_promedio)}</td><td><strong>Bs ${fmtNum(v.valor)}</strong></td>
            </tr>`).join('') || '<tr><td colspan="4" class="empty">Sin productos con stock</td></tr>';

        const venc = await request(API + '/reportes/vencimientos');
        $('#rep-vencimientos').innerHTML = venc.map((v) => `
            <tr><td>${v.nombre}</td><td>${fmtDate(v.vencimiento)}</td><td>${v.stock} ${v.unidad}</td></tr>`).join('')
            || '<tr><td colspan="3" class="empty">Sin productos con vencimiento</td></tr>';

        const repartos = await request(API + '/reportes/repartos?' + qs.toString());
        $('#rep-repartos').innerHTML = repartos.map((r) => `
            <tr>
                <td>${r.nombre}${r.principal ? ' <span class="badge badge-bajo">Principal</span>' : ''}</td>
                <td>${r.principal ? 'Principal' : 'Sucursal'}</td>
                <td>${r.num_repartos}</td>
                <td><strong>Bs ${fmtNum(r.total_repartido)}</strong></td>
            </tr>`).join('')
            || '<tr><td colspan="4" class="empty">Sin datos</td></tr>';

        const ganancias = await request(API + '/reportes/ganancias?' + qs.toString());
        let totVenta = 0, totCosto = 0, totUtil = 0;
        $('#rep-ganancias').innerHTML = ganancias.map((g) => {
            totVenta += g.venta; totCosto += g.costo; totUtil += g.utilidad;
            return `<tr>
                <td><strong>${esc(g.nombre)}</strong></td>
                <td>${g.cantidad}</td>
                <td>Bs ${fmtNum(g.venta)}</td>
                <td>Bs ${fmtNum(g.costo)}</td>
                <td><span class="${g.utilidad < 0 ? 'text-red' : ''}"><strong>Bs ${fmtNum(g.utilidad)}</strong></span></td>
            </tr>`;
        }).join('') || '<tr><td colspan="5" class="empty">Sin ventas en el período</td></tr>';
        if (ganancias.length) {
            $('#rep-ganancias').innerHTML += `
                <tr class="total-row">
                    <td><strong>TOTAL</strong></td><td></td>
                    <td><strong>Bs ${fmtNum(totVenta)}</strong></td>
                    <td><strong>Bs ${fmtNum(totCosto)}</strong></td>
                    <td><strong>Bs ${fmtNum(totUtil)}</strong></td>
                </tr>`;
        }
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#btn-generar-reporte').addEventListener('click', loadReportes);
$('#btn-exportar-rep-sucursal').addEventListener('click', () => {
    window.location.href = '/api/exportar/repartos?desde=' + $('#rep-desde').value + '&hasta=' + $('#rep-hasta').value;
});
$('#btn-exportar-ganancias').addEventListener('click', () => {
    window.location.href = '/api/exportar/ganancias?desde=' + $('#rep-desde').value + '&hasta=' + $('#rep-hasta').value;
});
$('#btn-imprimir-reporte').addEventListener('click', () => window.print());
$('#btn-etiquetas').addEventListener('click', () => window.open('/etiquetas', '_blank'));

// ---------------- Sincronía de inventario (admins) ----------------
function hacerFicha(n, ok, lab) {
    return `<div class="sinc-ficha ${ok ? 'sinc-ok' : 'sinc-mal'}"><div class="sinc-num">${n}</div><div class="sinc-lab">${esc(lab)}</div></div>`;
}

function renderAuditoria(a, titulo) {
    const vm = +a.ventas.m_total + (+a.ventas.m_vacio);
    const rm = +a.repartos.m_total + (+a.repartos.m_vacio);
    let html = `<h3>${esc(titulo)}</h3><div class="sinc-fichas">`;
    html += hacerFicha(a.descuadres.length, a.descuadres.length === 0, 'Descuadres stock/mov.');
    html += hacerFicha(vm, vm === 0, 'Ventas con error');
    html += hacerFicha(rm, rm === 0, 'Repartos con error');
    html += hacerFicha(a.productos_con_vencimiento, true, 'Con vencimiento');
    html += `</div>`;
    html += `<p class="sinc-val"><strong>Valorización total: Bs ${fmtNum(a.total_valorizacion)}</strong> · Productos con stock: ${a.productos_con_stock} · Proveedores: ${a.proveedores}</p>`;
    if (a.valorizacion.length) {
        html += `<table class="data-table"><thead><tr><th>Sucursal</th><th>Unidades</th><th>Valor (Bs)</th></tr></thead><tbody>` +
            a.valorizacion.map((v) => `<tr><td>${esc(v.sucursal)}</td><td>${v.unid}</td><td><strong>Bs ${fmtNum(v.valor)}</strong></td></tr>`).join('') + `</tbody></table>`;
    }
    if (a.descuadres.length) {
        html += `<table class="data-table"><thead><tr><th>Producto</th><th>Sucursal</th><th>Stock tabla</th><th>Según movimientos</th><th>Diferencia</th></tr></thead><tbody>` +
            a.descuadres.map((d) => `<tr class="sinc-mal-row"><td>${esc(d.nombre)}</td><td>${esc(d.sucursal)}</td><td>${d.stock_tab}</td><td>${d.mov}</td><td><strong>${d.dif}</strong></td></tr>`).join('') + `</tbody></table>`;
    }
    return html;
}

async function cargarAuditoria() {
    const caja = $('#sincronia-resultado');
    caja.innerHTML = 'Auditando...';
    try {
        const a = await request(API + '/reportes/auditoria');
        caja.innerHTML = renderAuditoria(a, 'Resultado de la auditoría');
    } catch (e) {
        caja.innerHTML = `<p class="text-red">Error: ${esc(e.message)}</p>`;
    }
}

async function reconciliarInventario() {
    if (!confirm('Se eliminarán movimientos huérfanos (ventas/repartos ya inexistentes), se recalculará el stock desde los movimientos y se re-calcularán los totales. ¿Continuar?')) return;
    const caja = $('#sincronia-resultado');
    caja.innerHTML = 'Reconciliando...';
    try {
        const r = await request(API + '/reportes/reconciliar', { method: 'POST' });
        let html = `<p class="sinc-val">Reconciliación aplicada: <strong>${r.movimientos_huerfanos_borrados}</strong> movimiento(s) huérfano(s) eliminados.</p>`;
        if (r.antes.descuadres.length) html += `<p>Descuadres antes: ${r.antes.descuadres.length} · Descuadres después: ${r.despues.descuadres.length}</p>`;
        caja.innerHTML = html + renderAuditoria(r.despues, 'Resultado de la reconciliación');
        loadReportes();
    } catch (e) {
        caja.innerHTML = `<p class="text-red">Error: ${esc(e.message)}</p>`;
    }
}

$('#btn-auditar').addEventListener('click', cargarAuditoria);
$('#btn-reconciliar').addEventListener('click', reconciliarInventario);

function hacerFicha(n, ok, lab) {
    return `<div class="sinc-ficha ${ok ? 'sinc-ok' : 'sinc-mal'}"><div class="sinc-num">${n}</div><div class="sinc-lab">${esc(lab)}</div></div>`;
}

async function cargarAuditoria() {
    const caja = $('#sincronia-resultado');
    caja.innerHTML = 'Auditando...';
    try {
        const a = await request(API + '/reportes/auditoria');
        caja.innerHTML = renderAuditoria(a, 'Auditoría');
    } catch (e) {
        caja.innerHTML = `<p class="text-red">Error: ${esc(e.message)}</p>`;
    }
}

async function reconciliarInventario() {
    if (!confirm('Se eliminarán movimientos huérfanos (ventas/repartos ya inexistentes), se recalculará el stock desde los movimientos y se re-calcularán los totales. ¿Continuar?')) return;
    const caja = $('#sincronia-resultado');
    caja.innerHTML = 'Reconciliando...';
    try {
        const r = await request(API + '/reportes/reconciliar', { method: 'POST' });
        let html = `<p class="sinc-val">Reconciliación aplicada: <strong>${r.movimientos_huerfanos_borrados}</strong> movimiento(s) huérfano(s) eliminados.</p>`;
        if (r.antes.descuadres.length) html += `<p>Descuadres antes: ${r.antes.descuadres.length} · Descuadres después: ${r.despues.descuadres.length}</p>`;
        caja.innerHTML = html + renderAuditoria(r.despues, 'Reconciliación');
        loadReportes();
    } catch (e) {
        caja.innerHTML = `<p class="text-red">Error: ${esc(e.message)}</p>`;
    }
}

$('#btn-auditar').addEventListener('click', cargarAuditoria);
$('#btn-reconciliar').addEventListener('click', reconciliarInventario);

// ---------------- Escaneo de código de barras ----------------
function abrirNuevoProductoConCodigo(codigo) {
    openProductoModal();
    $('#prod-codigo').value = codigo;
    $('#prod-nombre').focus();
}

async function buscarProductoPorCodigo(codigo) {
    return await request(API + '/productos/codigo?codigo=' + encodeURIComponent(codigo));
}

function vincularEscaneo(inputSel, selectSel, cantSel) {
    const input = $(inputSel);
    input.addEventListener('keydown', async (e) => {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const codigo = input.value.trim();
        input.value = '';
        if (!codigo) return;
        try {
            const p = await buscarProductoPorCodigo(codigo);
            const sel = $(selectSel);
            if (!sel.querySelector(`option[value="${p.id}"]`)) {
                toast('Ese producto no pertenece a esta sucursal', 'err');
                return;
            }
            sel.value = p.id;
            sel.dispatchEvent(new Event('change'));
            const cant = $(cantSel);
            cant.focus();
            cant.select();
            toast('Producto: ' + p.nombre + ' (stock: ' + p.stock + ')');
        } catch (err) {
            toast('Código no registrado. Completa el formulario para crear el producto', 'err');
            abrirNuevoProductoConCodigo(codigo);
        }
    });
    input.addEventListener('click', () => input.select());
}

// Atajo "/" para enfocar el escáner de la vista activa
document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const activa = document.querySelector('.view.active');
    if (!activa) return;
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
    const scan = activa.querySelector('.scan-input input');
    if (scan) { e.preventDefault(); scan.focus(); scan.select(); }
});

// ---------------- Ventas ----------------
let ventaItems = [];

async function loadVentas() {
    try {
        await loadCatalogos();
        const respP = await request(API + '/productos?por_pagina=1000&stock_sucursal=' + (window.SUCURSAL_ID || ''));
        const prods = (respP.data || respP).filter((p) => p.stock > 0);
        $('#venta-producto').innerHTML = prods.length
            ? '<option value="">Seleccione producto...</option>' +
                prods.map((p) => `<option value="${p.id}" data-precio="${p.precio_venta || ''}" data-stock="${p.stock}">${nomProd(p)} (stock: ${p.stock} ${p.unidad})</option>`).join('')
            : '<option value="">No tienes productos con stock</option>';
        await listarVentas();
    } catch (e) {
        toast(e.message, 'err');
    }
}

function actVentaItems() {
    $('#venta-items-tbody').innerHTML = ventaItems.length ? ventaItems.map((it, i) => `
        <tr>
            <td><strong>${it.nombre}</strong></td>
            <td>${it.cantidad}</td>
            <td>Bs ${fmtNum(it.precio_unitario)}</td>
            <td>Bs ${fmtNum(it.cantidad * it.precio_unitario)}</td>
            <td><button class="btn btn-icon btn-danger" onclick="quitarItemVenta(${i})">Quitar</button></td>
        </tr>`).join('')
        : '<tr><td colspan="5" class="empty">Agrega productos a la venta</td></tr>';
    const total = ventaItems.reduce((s, it) => s + it.cantidad * it.precio_unitario, 0);
    $('#venta-total').textContent = 'Bs ' + fmtNum(total);
}

window.quitarItemVenta = (i) => { ventaItems.splice(i, 1); actVentaItems(); };

$('#btn-agregar-item').addEventListener('click', () => {
    const sel = $('#venta-producto');
    const opt = sel.options[sel.selectedIndex];
    const cantidad = +$('#venta-cantidad').value || 0;
    if (!opt || !opt.value) return toast('Seleccione un producto', 'err');
    if (cantidad <= 0) return toast('Ingrese una cantidad', 'err');
    if (cantidad > +opt.dataset.stock) return toast('Stock insuficiente', 'err');
    const precio = +$('#venta-precio').value || +opt.dataset.precio || 0;
    if (precio <= 0) return toast('Ingrese un precio', 'err');
    ventaItems.push({ producto_id: +opt.value, nombre: opt.textContent.split(' (')[0], cantidad, precio_unitario: precio });
    $('#venta-cantidad').value = '';
    $('#venta-precio').value = '';
    actVentaItems();
});

$('#venta-producto').addEventListener('change', () => {
    const sel = $('#venta-producto');
    const opt = sel.options[sel.selectedIndex];
    if (opt && opt.dataset.precio) $('#venta-precio').value = opt.dataset.precio;
});

$('#form-venta').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!ventaItems.length) return toast('La venta no tiene productos', 'err');
    try {
        const res = await request(API + '/ventas', {
            method: 'POST',
            body: JSON.stringify({
                fecha: fechaISO($('#venta-fecha').value) || undefined,
                nota: $('#venta-nota').value,
                detalle: ventaItems,
            }),
        });
        toast('Venta registrada por Bs ' + fmtNum(res.total));
        ventaItems = [];
        actVentaItems();
        e.target.reset();
        $('#venta-fecha').value = nowLocal();
        listarVentas();
    } catch (err) {
        toast(err.message, 'err');
    }
});

async function listarVentas() {
    const esGestion = (window.ROL === 'admin' || window.ROL === 'superadmin');
    const tabs = $('#venta-tabs'), select = $('#venta-sucursal-select');
    if (tabs) {
        const btnSuc = $('#btn-venta-sucursales');
        if (esGestion) {
            tabs.style.display = 'flex';
            select.style.display = (btnSuc && btnSuc.classList.contains('active')) ? 'inline-flex' : 'none';
        } else {
            tabs.style.display = 'none';
        }
    }
    const qs = new URLSearchParams();
    const filtro = ($('#venta-filtro') || {}).value?.trim() || '';
    if (filtro) qs.set('filtro', filtro);
    if (($('#venta-desde') || {}).value) qs.set('desde', $('#venta-desde').value);
    if ($('#venta-hasta').value) qs.set('hasta', $('#venta-hasta').value);
    if (esGestion && select && select.value) qs.set('sucursal_id', select.value);
    qs.set('pagina', pagState['#ventas-paginacion'] || 1);
    const resp = await request(API + '/ventas?' + qs.toString());
    const ventas = resp.data || resp;
    const total = resp.total || ventas.length;
    const pagina = resp.pagina || 1;
    const porPagina = resp.por_pagina || 50;
    $('#ventas-tbody').innerHTML = ventas.map((v) => `
        <tr>
            <td>#${v.id}</td>
            <td>${fmtDate(v.fecha)}</td>
            <td class="items-detalle">${esc(v.items_detalle) || v.num_items + ' items'}</td>
            <td><strong>Bs ${fmtNum(v.total)}</strong></td>
            <td>${v.sucursal_nombre || '—'}</td>
            <td>${esc(v.nota) || '—'}</td>
            <td>${v.usuario || '—'}</td>
            <td>
                <button class="btn btn-icon" onclick="verVenta(${v.id})" title="Ver detalle"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
                ${window.ROL !== 'encargado' ? `<button class="btn btn-icon btn-danger" onclick="anularVenta(${v.id})" title="Anular venta"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z"/></svg></button>` : ''}
            </td>
        </tr>`).join('') || '<tr><td colspan="8" class="empty">Sin ventas registradas</td></tr>';
    renderPagination('#ventas-paginacion', total, pagina, porPagina, listarVentas);
}

$('#btn-venta-mio').addEventListener('click', () => {
    $('#btn-venta-mio').classList.add('active');
    $('#btn-venta-sucursales').classList.remove('active');
    $('#venta-sucursal-select').style.display = 'none';
    $('#venta-sucursal-select').value = '';
    pagState['#ventas-paginacion'] = 1;
    listarVentas();
});
$('#btn-venta-sucursales').addEventListener('click', () => {
    $('#btn-venta-sucursales').classList.add('active');
    $('#btn-venta-mio').classList.remove('active');
    $('#venta-sucursal-select').style.display = 'inline-flex';
    pagState['#ventas-paginacion'] = 1;
    listarVentas();
});
$('#venta-sucursal-select').addEventListener('change', () => {
    pagState['#ventas-paginacion'] = 1;
    listarVentas();
});

window.anularVenta = async (id) => {
    if (!confirm('¿Anular la venta #' + id + '? Se repondrá el stock.')) return;
    try {
        const res = await request(API + '/ventas/' + id, { method: 'DELETE' });
        toast(res.message, 'ok');
        listarVentas();
        loadDashboard();
    } catch (e) {
        toast(e.message, 'err');
    }
};

window.verVenta = async (id) => {
    try {
        const data = await request(API + '/ventas/' + id);
        const v = data.venta;
        $('#det-venta-title').textContent = 'Detalle de venta #' + v.id;
        $('#det-venta-fecha').textContent = fmtDate(v.fecha);
        $('#det-venta-usuario').textContent = v.usuario || '—';
        $('#det-venta-nota').textContent = v.nota || 'Sin nota';
        $('#det-venta-estado').textContent = v.id ? 'Registrada' : '—';
        $('#det-venta-total').textContent = fmtNum(v.total);
        $('#det-venta-items').innerHTML = data.detalle.map((d) => `
            <tr>
                <td>${esc(d.producto_nombre)}</td>
                <td>${d.cantidad}</td>
                <td>${fmtNum(d.precio_unitario)}</td>
                <td><strong>${fmtNum(d.subtotal)}</strong></td>
            </tr>`).join('') || '<tr><td colspan="4" class="empty">Sin items</td></tr>';
        $('#modal-detalle-venta').classList.add('open');
    } catch (e) {
        toast(e.message, 'err');
    }
};

$('#btn-filtrar-ventas').addEventListener('click', () => { pagState['#ventas-paginacion'] = 1; listarVentas(); });
on('#venta-filtro', 'input', debounce(() => { pagState['#ventas-paginacion'] = 1; listarVentas(); }, 300));
$('#btn-exportar-ventas').addEventListener('click', () => {
    const select = $('#venta-sucursal-select');
    const enSucursales = select && select.value;
    const qs = new URLSearchParams();
    if ($('#venta-desde').value) qs.set('desde', $('#venta-desde').value);
    if ($('#venta-hasta').value) qs.set('hasta', $('#venta-hasta').value);
    if (enSucursales) qs.set('sucursal_id', select.value);
    const filtro = (($('#venta-filtro') || {}).value || '').trim();
    if (filtro) qs.set('filtro', filtro);
    window.location.href = '/api/exportar/ventas?' + qs.toString();
});
vincularEscaneo('#venta-escaneo', '#venta-producto', '#venta-cantidad');

// ---------------- Repartos ----------------
let repartoItems = [];

async function loadRepartos() {
    try {
        await loadCatalogos();
        const respP = await request(API + '/productos?por_pagina=1000&stock_sucursal=' + (window.SUCURSAL_ID || ''));
        const prods = respP.data || respP;
        const due = (p) => (p.sucursal_id && p.sucursal_id !== window.SUCURSAL_ID)
            ? ` · dueño: ${p.sucursal_nombre || p.sucursal_id}` : '';
        $('#reparto-producto').innerHTML = '<option value="">Seleccione producto...</option>' +
            prods.map((p) => `<option value="${p.id}" data-costo="${p.costo_promedio || ''}" data-stock="${p.stock}">${nomProd(p)}${due(p)} (stock: ${p.stock} ${p.unidad})</option>`).join('');
        const sucursales = await request(API + '/sucursales');
        const esGestion = (window.ROL === 'admin' || window.ROL === 'superadmin');
        const esPrincipal = !!window.SUCURSAL_PRINCIPAL;
        const destino = sucursales.filter((s) => s.id !== window.SUCURSAL_ID && (esGestion || esPrincipal || !s.principal));
        $('#reparto-sucursal').innerHTML = destino.map((s) =>
            `<option value="${s.id}">${s.principal ? '★ ' : ''}${s.nombre}</option>`).join('');
        $('#repartos-info').textContent = `Cochabamba · ${sucursales.length} sucursales`;
        if (window.ROL === 'superadmin') {
            $('#btn-gestionar-sucursales').style.display = 'inline-flex';
        }
        // Pestañas Mi Almacén / Almacén de Sucursales (solo admin/superadmin)
        const tabs = $('#reparto-tabs'), select = $('#reparto-sucursal-select');
        if (tabs) {
            if (esGestion) {
                tabs.style.display = 'flex';
                const btnSuc = $('#btn-reparto-sucursales');
                select.style.display = (btnSuc && btnSuc.classList.contains('active')) ? 'inline-flex' : 'none';
            } else {
                tabs.style.display = 'none';
            }
        }
        await listarRepartos();
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#btn-reparto-mio').addEventListener('click', () => {
    $('#btn-reparto-mio').classList.add('active');
    $('#btn-reparto-sucursales').classList.remove('active');
    $('#reparto-sucursal-select').style.display = 'none';
    $('#reparto-sucursal-select').value = '';
    listarRepartos();
});
$('#btn-reparto-sucursales').addEventListener('click', () => {
    $('#btn-reparto-sucursales').classList.add('active');
    $('#btn-reparto-mio').classList.remove('active');
    $('#reparto-sucursal-select').style.display = 'inline-flex';
    listarRepartos();
});
$('#reparto-sucursal-select').addEventListener('change', () => {
    listarRepartos();
});

function actRepartoItems() {
    $('#reparto-items-tbody').innerHTML = repartoItems.length ? repartoItems.map((it, i) => `
        <tr>
            <td><strong>${esc(it.nombre)}</strong></td>
            <td>${it.cantidad}</td>
            <td>Bs ${fmtNum(it.costo_unitario)}</td>
            <td>Bs ${fmtNum(it.cantidad * it.costo_unitario)}</td>
            <td><button class="btn btn-icon btn-danger" onclick="quitarItemReparto(${i})">Quitar</button></td>
        </tr>`).join('')
        : '<tr><td colspan="5" class="empty">Agrega productos al reparto</td></tr>';
    const total = repartoItems.reduce((s, it) => s + it.cantidad * it.costo_unitario, 0);
    $('#reparto-total').textContent = 'Bs ' + fmtNum(total);
}

window.quitarItemReparto = (i) => { repartoItems.splice(i, 1); actRepartoItems(); };

$('#btn-agregar-item-reparto').addEventListener('click', () => {
    const sel = $('#reparto-producto');
    const opt = sel.options[sel.selectedIndex];
    const cantidad = +$('#reparto-cantidad').value || 0;
    if (!opt || !opt.value) return toast('Seleccione un producto', 'err');
    if (cantidad <= 0) return toast('Ingrese una cantidad', 'err');
    if (cantidad > +opt.dataset.stock) return toast('Stock insuficiente', 'err');
    const costo = +$('#reparto-costo').value || +opt.dataset.costo || 0;
    repartoItems.push({ producto_id: +opt.value, nombre: opt.textContent.split(' (')[0], cantidad, costo_unitario: costo });
    $('#reparto-cantidad').value = '';
    $('#reparto-costo').value = '';
    actRepartoItems();
});

$('#reparto-producto').addEventListener('change', () => {
    const sel = $('#reparto-producto');
    const opt = sel.options[sel.selectedIndex];
    if (opt && opt.dataset.costo) $('#reparto-costo').value = opt.dataset.costo;
});

$('#form-reparto').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!repartoItems.length) return toast('El reparto no tiene productos', 'err');
    try {
        const res = await request(API + '/repartos', {
            method: 'POST',
            body: JSON.stringify({
                fecha: fechaISO($('#reparto-fecha').value) || undefined,
                sucursal_id: +$('#reparto-sucursal').value,
                nota: $('#reparto-nota').value,
                detalle: repartoItems,
            }),
        });
        toast('Reparto registrado por Bs ' + fmtNum(res.total));
        repartoItems = [];
        actRepartoItems();
        e.target.reset();
        $('#reparto-fecha').value = nowLocal();
        listarRepartos();
    } catch (err) {
        toast(err.message, 'err');
    }
});

async function listarRepartos() {
    const qs = new URLSearchParams();
    const filtro = ($('#reparto-filtro') || {}).value?.trim() || '';
    if (filtro) qs.set('filtro', filtro);
    if (($('#reparto-desde') || {}).value) qs.set('desde', $('#reparto-desde').value);
    if ($('#reparto-hasta').value) qs.set('hasta', $('#reparto-hasta').value);
    // Filtro por sucursal: "Almacén de Sucursales" activo + sucursal elegida
    const tabSuc = $('#btn-reparto-sucursales');
    const sel = $('#reparto-sucursal-select');
    if (tabSuc && tabSuc.classList.contains('active') && sel && sel.value) {
        qs.set('sucursal_id', sel.value);
    }
    qs.set('pagina', pagState['#repartos-paginacion'] || 1);
    const resp = await request(API + '/repartos?' + qs.toString());
    const repartos = resp.data || resp;
    const total = resp.total || repartos.length;
    const pagina = resp.pagina || 1;
    const porPagina = resp.por_pagina || 50;
    $('#repartos-tbody').innerHTML = repartos.map((r) => `
        <tr>
            <td>#${r.id}</td>
            <td>${fmtDate(r.fecha)}</td>
            <td><strong>${esc(r.origen_nombre || '—')} → ${esc(r.sucursal_nombre)}</strong></td>
            <td class="items-detalle">${esc(r.items_detalle) || r.num_items + ' items'}</td>
            <td><strong>Bs ${fmtNum(r.total)}</strong></td>
            <td>${esc(r.nota) || '—'}</td>
            <td>${r.usuario || '—'}</td>
            <td>
                <button class="btn btn-icon" onclick="verReparto(${r.id})" title="Ver detalle"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
                ${window.ROL === 'superadmin' ? `<button class="btn btn-icon btn-danger" onclick="anularReparto(${r.id})" title="Anular reparto"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z"/></svg></button>` : ''}
            </td>
        </tr>`).join('') || '<tr><td colspan="8" class="empty">Sin repartos registrados</td></tr>';
    renderPagination('#repartos-paginacion', total, pagina, porPagina, listarRepartos);
}

window.anularReparto = async (id) => {
    if (!confirm('¿Anular el reparto #' + id + '? El stock volverá al almacén.')) return;
    try {
        const res = await request(API + '/repartos/' + id, { method: 'DELETE' });
        toast(res.message, 'ok');
        listarRepartos();
        loadDashboard();
    } catch (e) {
        toast(e.message, 'err');
    }
};

window.verReparto = async (id) => {
    try {
        const data = await request(API + '/repartos/' + id);
        const r = data.reparto;
        $('#det-reparto-title').textContent = 'Detalle de reparto #' + r.id;
        $('#det-reparto-fecha').textContent = fmtDate(r.fecha);
        $('#det-reparto-sucursal').textContent = r.sucursal_nombre || '—';
        $('#det-reparto-usuario').textContent = r.usuario || '—';
        $('#det-reparto-nota').textContent = r.nota || 'Sin nota';
        $('#det-reparto-estado').textContent = r.id ? 'Registrado' : '—';
        $('#det-reparto-total').textContent = fmtNum(r.total);
        $('#det-reparto-items').innerHTML = data.detalle.map((d) => `
            <tr>
                <td>${esc(d.producto_nombre)}</td>
                <td>${d.cantidad}</td>
                <td>${fmtNum(d.costo_unitario)}</td>
                <td><strong>${fmtNum(d.subtotal)}</strong></td>
            </tr>`).join('') || '<tr><td colspan="4" class="empty">Sin items</td></tr>';
        $('#modal-detalle-reparto').classList.add('open');
    } catch (e) {
        toast(e.message, 'err');
    }
};

$('#btn-filtrar-repartos').addEventListener('click', () => { pagState['#repartos-paginacion'] = 1; listarRepartos(); });
on('#reparto-filtro', 'input', debounce(() => { pagState['#repartos-paginacion'] = 1; listarRepartos(); }, 300));
$('#btn-exportar-repartos').addEventListener('click', () => {
    const select = $('#reparto-sucursal-select');
    const enSucursales = $('.menu-btn').length && $('#btn-reparto-sucursales') && $('#btn-reparto-sucursales').classList.contains('active') && select.value;
    window.location.href = '/api/exportar/repartos?desde=' + $('#reparto-desde').value + '&hasta=' + $('#reparto-hasta').value + (enSucursales ? '&sucursal_id=' + select.value : '');
});
vincularEscaneo('#reparto-escaneo', '#reparto-producto', '#reparto-cantidad');

// ---------------- Gestión de sucursales (admin) ----------------
$('#btn-gestionar-sucursales').addEventListener('click', () => {
    $('#modal-sucursales').classList.add('open');
    cargarSucursales();
});

async function cargarSucursales() {
    const sucursales = await request(API + '/sucursales');
    $('#sucursales-tbody').innerHTML = sucursales.map((s) => `
        <tr>
            <td><strong>${s.nombre}</strong></td>
            <td>${s.direccion || '—'}</td>
            <td><span class="badge ${s.principal ? 'badge-bajo' : 'badge-entrada'}">${s.principal ? 'Principal' : 'Sucursal'}</span></td>
            <td>${s.num_repartos}</td>
            <td>Bs ${fmtNum(s.total_repartido)}</td>
            <td>
                <button class="btn btn-icon" data-edit-suc="${s.id}" title="Editar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg></button>
                <button class="btn btn-icon btn-danger" data-del-suc="${s.id}" title="Eliminar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
            </td>
        </tr>`).join('');
    $$('[data-edit-suc]').forEach((b) => b.addEventListener('click', () => {
        const s = sucursales.find((x) => x.id === Number(b.dataset.editSuc));
        $('#suc-nombre').value = s.nombre;
        $('#suc-direccion').value = s.direccion;
        $('#suc-principal').checked = !!s.principal;
        $('#btn-guardar-sucursal').dataset.edit = s.id;
    }));
    $$('[data-del-suc]').forEach((b) => b.addEventListener('click', async () => {
        if (!confirm('¿Eliminar esta sucursal?')) return;
        try {
            await request(API + '/sucursales/' + b.dataset.delSuc, { method: 'DELETE' });
            toast('Sucursal eliminada');
            cargarSucursales();
            if ($('#view-repartos').classList.contains('active')) loadRepartos();
        } catch (e) {
            toast(e.message, 'err');
        }
    }));
}

$('#btn-guardar-sucursal').addEventListener('click', async () => {
    const editId = $('#btn-guardar-sucursal').dataset.edit;
    const body = {
        nombre: $('#suc-nombre').value.trim(),
        direccion: $('#suc-direccion').value.trim(),
        principal: $('#suc-principal').checked ? 1 : 0,
    };
    if (!body.nombre) return toast('Ingresa el nombre de la sucursal', 'err');
    try {
        if (editId) {
            await request(API + '/sucursales/' + editId, { method: 'PUT', body: JSON.stringify(body) });
        } else {
            await request(API + '/sucursales', { method: 'POST', body: JSON.stringify(body) });
        }
        toast(editId ? 'Sucursal actualizada' : 'Sucursal creada');
        $('#suc-nombre').value = '';
        $('#suc-direccion').value = '';
        $('#suc-principal').checked = false;
        delete $('#btn-guardar-sucursal').dataset.edit;
        cargarSucursales();
        loadRepartos();
    } catch (e) {
        toast(e.message, 'err');
    }
});

// ---------------- Usuarios ----------------
async function loadUsuarios() {
    try {
        const users = await request(API + '/usuarios');
        $('#usuarios-tbody').innerHTML = users.map((u) => `
            <tr>
                <td><strong>${u.usuario}</strong></td>
                <td>${u.nombre || '—'}</td>
                <td><span class="badge ${u.rol === 'superadmin' ? 'badge-info' : u.rol === 'admin' ? 'badge-bajo' : 'badge-entrada'}">${u.rol === 'superadmin' ? 'Superadministrador' : u.rol === 'admin' ? 'Administrador' : 'Encargado'}</span></td>
                <td><span class="badge ${u.activo ? 'badge-entrada' : 'badge-salida'}">${u.activo ? 'Activo' : 'Inactivo'}</span></td>
                <td>
                    <button class="btn btn-icon" data-edit-user="${u.id}">Editar</button>
                    <button class="btn btn-icon btn-danger" data-del-user="${u.id}">Eliminar</button>
                </td>
            </tr>`).join('') || '<tr><td colspan="5" class="empty">Sin usuarios</td></tr>';

        $$('[data-edit-user]').forEach((b) => b.addEventListener('click', () => {
            const u = users.find((x) => x.id === Number(b.dataset.editUser));
            $('#user-id').value = u.id;
            $('#user-usuario').value = u.usuario;
            $('#user-usuario').disabled = true;
            $('#user-nombre').value = u.nombre;
            const rolSel = $('#user-rol');
            const opcAdmin = rolSel.querySelector('option[value="admin"]');
            const opcSuper = rolSel.querySelector('option[value="superadmin"]');
            if (window.ROL === 'superadmin') {
                opcAdmin.style.display = '';
                opcSuper.style.display = '';
            } else {
                opcAdmin.style.display = 'none';
                opcSuper.style.display = 'none';
            }
            rolSel.value = u.rol;
            $('#user-activo').value = u.activo ? '1' : '0';
            $('#user-password').value = '';
            $('#user-password').placeholder = 'Dejar en blanco para no cambiar';
            $('#modal-usuario-title').textContent = 'Editar usuario';
            $('#modal-usuario').classList.add('open');
        }));
        $$('[data-del-user]').forEach((b) => b.addEventListener('click', async () => {
            if (!confirm('¿Eliminar este usuario?')) return;
            try {
                await request(API + '/usuarios/' + b.dataset.delUser, { method: 'DELETE' });
                toast('Usuario eliminado');
                loadUsuarios();
            } catch (e) {
                toast(e.message, 'err');
            }
        }));
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#btn-nuevo-usuario').addEventListener('click', () => {
    const form = $('#form-usuario');
    form.reset();
    const rolSel = $('#user-rol');
    const opcAdmin = rolSel.querySelector('option[value="admin"]');
    const opcSuper = rolSel.querySelector('option[value="superadmin"]');
    if (window.ROL === 'superadmin') {
        opcAdmin.style.display = '';
        opcSuper.style.display = '';
        rolSel.value = 'encargado';
    } else {
        opcAdmin.style.display = 'none';
        opcSuper.style.display = 'none';
        rolSel.value = 'encargado';
    }
    $('#user-id').value = '';
    $('#user-usuario').disabled = false;
    $('#user-password').placeholder = 'Mínimo 4 caracteres';
    $('#modal-usuario-title').textContent = 'Nuevo usuario';
    $('#modal-usuario').classList.add('open');
});

$('#form-usuario').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = $('#user-id').value;
    const body = {
        usuario: $('#user-usuario').value,
        nombre: $('#user-nombre').value,
        rol: $('#user-rol').value,
        activo: +$('#user-activo').value,
        password: $('#user-password').value,
    };
    try {
        if (id) {
            await request(API + '/usuarios/' + id, { method: 'PUT', body: JSON.stringify(body) });
        } else {
            await request(API + '/usuarios', { method: 'POST', body: JSON.stringify(body) });
        }
        toast(id ? 'Usuario actualizado' : 'Usuario creado');
        $('#modal-usuario').classList.remove('open');
        loadUsuarios();
    } catch (err) {
        toast(err.message, 'err');
    }
});

// ---------------- Auditoría ----------------
async function loadAuditoria() {
    try {
        const registros = await request(API + '/auditoria?limite=300');
        $('#auditoria-tbody').innerHTML = registros.map((r) => `
            <tr>
                <td>${fmtDate(r.fecha)}</td>
                <td><strong>${r.usuario || '—'}</strong></td>
                <td>${r.accion}</td>
                <td>${r.detalle || ''}</td>
            </tr>`).join('') || '<tr><td colspan="4" class="empty">Sin registros</td></tr>';
    } catch (e) {
        toast(e.message, 'err');
    }
}
$('#btn-refrescar-auditoria').addEventListener('click', loadAuditoria);

// ---------------- Respaldo ----------------
$('#btn-backup').addEventListener('click', () => {
    window.location.href = '/api/backup';
});

$('#btn-restaurar').addEventListener('click', async () => {
    const archivo = $('#archivo-restaurar').files[0];
    if (!archivo) return toast('Selecciona un archivo .db', 'err');
    if (!confirm('¿Restaurar esta base de datos? Se reemplazará la actual. Antes se hará una copia automática.')) return;
    const fd = new FormData();
    fd.append('archivo', archivo);
    try {
        const res = await fetch('/api/restaurar', { method: 'POST', body: fd });
        const json = await res.json();
        if (!json.ok) throw new Error(json.message);
        toast('Base de datos restaurada');
        setTimeout(() => window.location.href = '/login', 1200);
    } catch (e) {
        toast(e.message, 'err');
    }
});

// ---------------- Utilidades ----------------
function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------------- Paginación ----------------
const pagState = {};
function renderPagination(containerId, total, pagina, porPagina, loadFn) {
    const el = $(containerId);
    if (!el) return;
    const paginas = Math.max(1, Math.ceil(total / porPagina));
    if (total <= porPagina) { el.innerHTML = ''; return; }
    el.innerHTML =
        '<button ' + (pagina <= 1 ? 'disabled' : '') + ' data-pag="' + (pagina - 1) + '">&laquo; Anterior</button>' +
        '<span class="pag-info">Página ' + pagina + ' de ' + paginas + ' (' + total + ' registros)</span>' +
        '<button ' + (pagina >= paginas ? 'disabled' : '') + ' data-pag="' + (pagina + 1) + '">Siguiente &raquo;</button>';
    el.querySelectorAll('button[data-pag]').forEach((b) => {
        b.addEventListener('click', () => {
            const p = parseInt(b.dataset.pag);
            if (p >= 1 && p <= paginas) { pagState[containerId] = p; loadFn(); }
        });
    });
}

// ---------------- Perfil y sesión ----------------
const tSes = $('#sidebar-sesion');
if (tSes) {
    tSes.addEventListener('click', async () => {
        try {
            const s = await request(API + '/sesion');
            const ini = esc((s.nombre || s.usuario || '?')[0].toUpperCase());
            $('#perfil-avatar-lg').textContent = (s.nombre || s.usuario || '?')[0].toUpperCase();
            $('#perfil-nombre').value = s.nombre || '';
            $('#perfil-pass-actual').value = '';
            $('#perfil-pass-nueva').value = '';
            $('#modal-perfil').classList.add('open');
        } catch (e) {
            toast('Error al cargar perfil', 'err');
        }
    });
}

$('#form-perfil').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        const body = { nombre: $('#perfil-nombre').value.trim() };
        const passActual = $('#perfil-pass-actual').value;
        const passNueva = $('#perfil-pass-nueva').value;
        if (passNueva) {
            if (!passActual) { toast('Ingresa tu contraseña actual', 'err'); return; }
            body.actual = passActual;
            body.nueva = passNueva;
        }
        await request(API + '/perfil', { method: 'PUT', body: JSON.stringify(body) });
        toast('Perfil actualizado');
        $('#modal-perfil').classList.remove('open');
        init();
    } catch (err) {
        toast(err.message, 'err');
    }
});

$('#btn-salir').addEventListener('click', async () => {
    try {
        await request(API + '/logout', { method: 'POST' });
    } catch (e) { /* ignora errores al salir */ }
    window.location.href = '/login';
});

$$('.close').forEach((c) => c.addEventListener('click', () => $('#' + c.dataset.close).classList.remove('open')));
window.addEventListener('click', (e) => {
    if (e.target.classList && e.target.classList.contains('modal')) e.target.classList.remove('open');
});

// Fechas por defecto en movimientos, gastos, ventas y repartos
$('#mov-fecha').value = nowLocal();
$('#gasto-fecha').value = nowLocal();
$('#venta-fecha').value = nowLocal();
$('#reparto-fecha').value = nowLocal();
$('#pedido-fecha').value = nowLocal();

// ---------------- Pedidos ----------------
let pedidoItems = [];
let pedidoProdsAll = [];

function popPedidoProductos(sid) {
    const sel = $('#pedido-producto');
    if (!sel) return;
    const lista = sid ? pedidoProdsAll.filter((p) => +p.sucursal_id === sid) : [];
    sel.innerHTML = !sid
        ? '<option value="">Primero selecciona la sucursal que provee...</option>'
        : (lista.length
            ? '<option value="">Seleccione producto...</option>' +
                lista.map((p) => `<option value="${p.id}">${nomProd(p)} (${p.unidad})</option>`).join('')
            : '<option value="">Esa sucursal no tiene productos aún</option>');
}

$('#pedido-destino').addEventListener('change', () => {
    popPedidoProductos(+$('#pedido-destino').value || null);
});

async function loadPedidos() {
    try {
        await loadCatalogos();
        const respP = await request(API + '/productos?por_pagina=1000');
        pedidoProdsAll = respP.data || respP;
        const sucursales = await request(API + '/sucursales');
        $('#pedido-destino').innerHTML = '<option value="">Seleccione la sucursal que provee...</option>' +
            sucursales.filter((s) => s.id !== window.SUCURSAL_ID).map((s) =>
                `<option value="${s.id}">${s.principal ? '★ ' : ''}${s.nombre}</option>`).join('');
        if (window.ROL === 'encargado') {
            const mie = sucursales.find((x) => x.id === window.SUCURSAL_ID);
            $('#pedido-sucursal').innerHTML = mie
                ? `<option value="${mie.id}">${mie.principal ? '★ ' : ''}${mie.nombre}</option>` : '';
            if (window.SUCURSAL_PRINCIPAL) {
                const form = $('#form-pedido');
                if (form && form.closest('.panel')) form.closest('.panel').style.display = 'none';
            }
        } else {
            // El almacén no pide; recibe y despacha pedidos de las sucursales
            if (window.SUCURSAL_PRINCIPAL) {
                const form = $('#form-pedido');
                if (form && form.closest('.panel')) form.closest('.panel').style.display = 'none';
            }
            $('#pedido-sucursal').innerHTML = sucursales.map((s) =>
                `<option value="${s.id}">${s.principal ? '★ ' : ''}${s.nombre}</option>`).join('');
        }
        popPedidoProductos(+$('#pedido-destino').value || null);
        $('#pedidos-info').textContent = `Tickets por pedido de sucursal.`;
        await listarPedidos();
    } catch (e) {
        toast(e.message, 'err');
    }
}

function actPedidoItems() {
    $('#pedido-items-tbody').innerHTML = pedidoItems.length ? pedidoItems.map((it, i) => `
        <tr>
            <td><strong>${esc(it.nombre)}</strong></td>
            <td>${it.cantidad}</td>
            <td><button class="btn btn-icon btn-danger" onclick="quitarItemPedido(${i})">Quitar</button></td>
        </tr>`).join('')
        : '<tr><td colspan="3" class="empty">Agrega productos al pedido</td></tr>';
}

window.quitarItemPedido = (i) => { pedidoItems.splice(i, 1); actPedidoItems(); };

$('#btn-agregar-item-pedido').addEventListener('click', () => {
    const sel = $('#pedido-producto');
    const opt = sel.options[sel.selectedIndex];
    const cantidad = +$('#pedido-cantidad').value || 0;
    if (!opt || !opt.value) return toast('Seleccione un producto', 'err');
    if (cantidad <= 0) return toast('Ingrese una cantidad', 'err');
    pedidoItems.push({ producto_id: +opt.value, nombre: opt.textContent.split(' (')[0], cantidad });
    $('#pedido-cantidad').value = '';
    actPedidoItems();
});

$('#form-pedido').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!pedidoItems.length) return toast('El pedido no tiene productos', 'err');
    const sucursal_id = +$('#pedido-sucursal').value;
    if (!sucursal_id) return toast('Seleccione la sucursal que hace el pedido', 'err');
    const destino_id = +$('#pedido-destino').value;
    if (window.ROL === 'encargado' && !destino_id) return toast('Seleccione la sucursal a la que va el pedido', 'err');
    try {
        const res = await request(API + '/pedidos', {
            method: 'POST',
            body: JSON.stringify({
                fecha: fechaISO($('#pedido-fecha').value) || undefined,
                sucursal_id,
                destino_id: destino_id || undefined,
                nota: $('#pedido-nota').value,
                detalle: pedidoItems,
            }),
        });
        toast(res.message, 'ok');
        pedidoItems = [];
        actPedidoItems();
        e.target.reset();
        $('#pedido-fecha').value = nowLocal();
        listarPedidos();
    } catch (err) {
        toast(err.message, 'err');
    }
});

async function listarPedidos() {
    const qs = new URLSearchParams();
    const filtro = ($('#pedido-filtro') || {}).value?.trim() || '';
    if (filtro) qs.set('filtro', filtro);
    const est = ($('#pedido-estado') || {}).value || '';
    if (est) qs.set('estado', est);
    qs.set('pagina', pagState['#pedidos-paginacion'] || 1);
    const resp = await request(API + '/pedidos?' + qs.toString());
    const pedidos = resp.data || resp;
    const total = resp.total || pedidos.length;
    const pagina = resp.pagina || 1;
    const porPagina = resp.por_pagina || 50;
    const badges = { pendiente: 'badge-pendiente', despachado: 'badge-despachado', cumplido: 'badge-cumplido' };
    $('#pedidos-tbody').innerHTML = pedidos.map((p) => `
        <tr>
            <td><strong>${esc(p.nro_ticket)}</strong></td>
            <td>${fmtDate(p.fecha)}</td>
            <td><strong>${esc(p.sucursal_nombre)}</strong></td>
            <td>${esc(p.destino_nombre) || '—'}</td>
            <td>${p.num_items} item(s)</td>
            <td><span class="${badges[p.estado] || 'badge-pendiente'}">${esc(p.estado)}</span></td>
            <td>${esc(p.nota) || '—'}</td>
            <td>
                <button class="btn btn-icon" onclick="verPedido(${p.id})" title="Ver detalle"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
            </td>
        </tr>`).join('') || '<tr><td colspan="8" class="empty">Sin pedidos registrados</td></tr>';
    renderPagination('#pedidos-paginacion', total, pagina, porPagina, listarPedidos);
    // contador de pendientes en el menú
    try {
        const p = await request(API + '/pedidos?estado=pendiente&pagina=1&por_pagina=1000');
        const n = (p.total != null ? p.total : (p.data || p).length);
        const badge = $('#badge-pedidos');
        if (badge) {
            badge.textContent = n > 0 ? n : '';
            badge.style.display = n > 0 ? 'inline-flex' : 'none';
        }
    } catch (_) { }
}

$('#btn-filtrar-pedidos').addEventListener('click', () => { pagState['#pedidos-paginacion'] = 1; listarPedidos(); });
on('#pedido-filtro', 'input', debounce(() => { pagState['#pedidos-paginacion'] = 1; listarPedidos(); }, 300));
on('#pedido-estado', 'change', () => { pagState['#pedidos-paginacion'] = 1; listarPedidos(); });

window.verPedido = async (id) => {
    try {
        const data = await request(API + '/pedidos/' + id);
        const p = data.pedido;
        $('#det-pedido-title').textContent = 'Detalle de pedido ' + p.nro_ticket;
        $('#det-pedido-nro').textContent = p.nro_ticket;
        $('#det-pedido-fecha').textContent = fmtDate(p.fecha);
        $('#det-pedido-sucursal').textContent = p.sucursal_nombre || '—';
        $('#det-pedido-destino').textContent = p.destino_nombre || '—';
        $('#det-pedido-usuario').textContent = p.usuario || '—';
        $('#det-pedido-estado').textContent = p.estado || '—';
        $('#det-pedido-nota').textContent = p.nota || 'Sin nota';
        $('#det-pedido-items').innerHTML = data.detalle.map((d) => `
            <tr><td>${esc(d.producto_nombre)}</td><td>${d.cantidad}</td></tr>`).join('');
        $('#modal-pedido').classList.add('open');
        window._pedidoActual = { id, estado: p.estado };
        const despacharBtn = $('#btn-pedido-despachar');
        const cambia = $('#pedido-cambiar-estado');
        const esAdmin = window.ROL === 'superadmin' || window.ROL === 'admin';
        const esAlmacenPrincipal = window.ROL === 'encargado' && window.SUCURSAL_PRINCIPAL;
        if (despacharBtn) despacharBtn.style.display = (esAdmin && p.estado === 'pendiente') ? 'inline-flex' : 'none';
        if (cambia) { cambia.value = ''; cambia.style.display = (esAdmin || esAlmacenPrincipal) ? 'inline-flex' : 'none'; }
    } catch (e) {
        toast(e.message, 'err');
    }
};

$('#btn-pedido-despachar').addEventListener('click', async () => {
    if (!window._pedidoActual) return;
    if (!confirm('¿Despachar el pedido? Se creará el reparto real y saldrá del almacén.')) return;
    try {
        const res = await request(API + '/pedidos/' + window._pedidoActual.id + '/despachar', { method: 'POST' });
        toast(res.message, 'ok');
        $('#modal-pedido').classList.remove('open');
        listarPedidos();
        loadDashboard();
    } catch (e) {
        toast(e.message, 'err');
    }
});

$('#pedido-cambiar-estado').addEventListener('change', async () => {
    if (!window._pedidoActual) return;
    const nuevo = $('#pedido-cambiar-estado').value;
    if (!nuevo || nuevo === window._pedidoActual.estado) return;
    try {
        const res = await request(API + '/pedidos/' + window._pedidoActual.id + '/estado', {
            method: 'PUT',
            body: JSON.stringify({ estado: nuevo }),
        });
        toast(res.message, 'ok');
        $('#modal-pedido').classList.remove('open');
        listarPedidos();
    } catch (e) {
        toast(e.message, 'err');
        $('#pedido-cambiar-estado').value = '';
    }
});

// ---------------- Aviso de tickets nuevos ----------------
let _lastTicketId = 0;

function beepTickets() {
    try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        const ctx = new Ctx();
        [880, 1174.66].forEach((f, i) => {
            const o = ctx.createOscillator();
            const g = ctx.createGain();
            o.type = 'sine';
            o.frequency.value = f;
            o.connect(g);
            g.connect(ctx.destination);
            const t = ctx.currentTime + i * 0.18;
            g.gain.setValueAtTime(0.0001, t);
            g.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
            g.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
            o.start(t);
            o.stop(t + 0.16);
        });
        setTimeout(() => { try { ctx.close(); } catch (_) { } }, 900);
    } catch (_) { }
}

async function syncPedidosNuevos() {
    try {
        const qs = new URLSearchParams({ pagina: 1, por_pagina: 10 });
        const resp = await request(API + '/pedidos?' + qs.toString());
        const pedidos = resp.data || resp;
        const maxId = pedidos.reduce((m, p) => Math.max(m, p.id || 0), 0);
        if (_lastTicketId) {
            const nuevos = pedidos
                .filter((p) => (p.id || 0) > _lastTicketId && p.usuario !== window.USUARIO)
                .sort((a, b) => a.id - b.id);
            nuevos.forEach((p) => {
                const dest = p.destino_nombre ? ' -> ' + p.destino_nombre : '';
                toast('Nuevo ticket ' + p.nro_ticket + ' de ' + p.sucursal_nombre + dest, 'ok');
            });
            if (nuevos.length) beepTickets();
        }
        _lastTicketId = Math.max(_lastTicketId, maxId);
        const pend = await request(API + '/pedidos?estado=pendiente&pagina=1&por_pagina=1000');
        const n = pend.total != null ? pend.total : (pend.data || pend).length;
        const badge = $('#badge-pedidos');
        if (badge) {
            badge.textContent = n > 0 ? n : '';
            badge.style.display = n > 0 ? 'inline-flex' : 'none';
        }
    } catch (_) { }
}

// ---------------- Inicio ----------------
async function init() {
    try {
        const s = await request(API + '/sesion');
        window.ROL = s.rol;
        window.USUARIO = s.usuario || '';
        window.SUCURSAL = s.sucursal_nombre || '';
        window.SUCURSAL_ID = s.sucursal_id || null;
        window.SUCURSAL_PRINCIPAL = !!s.sucursal_principal;
        const ocultar = (v) => {
            const btn = document.querySelector(`.menu-btn[data-view="${v}"]`);
            if (btn) btn.style.display = 'none';
        };
        // Admin y encargado no gestionan administración global
        if (s.rol !== 'superadmin') {
            ['usuarios', 'auditoria', 'respaldo', 'almacenes', 'categorias'].forEach(ocultar);
        }
        // Encargado: solo operación de su sucursal (sin reportes)
        if (s.rol === 'encargado') {
            ['reportes'].forEach(ocultar);
            const nuevoProd = $('#btn-nuevo-producto');
            if (nuevoProd) nuevoProd.style.display = '';
        }
        // Sincronía de inventario: solo administradores
        if (s.rol === 'admin' || s.rol === 'superadmin') {
            const p = $('#panel-sincronia');
            if (p) p.style.display = '';
        }
        // Solo el superadmin gestiona sucursales / reparte desde el almacén principal
        if (s.rol !== 'superadmin') {
            const g = $('#btn-gestionar-sucursales');
            if (g) g.style.display = 'none';
        }
        const tSes = $('#sidebar-sesion');
        if (tSes) {
            let rol;
            if (s.rol === 'superadmin') rol = 'Superadministrador';
            else if (s.rol === 'admin') rol = 'Administrador';
            else rol = 'Encargado';
            const ini = esc((s.nombre || s.usuario || '?')[0].toUpperCase());
            const suc = window.SUCURSAL ? `<div class="sesion-suc">${esc(window.SUCURSAL)}</div>` : '';
            tSes.innerHTML = '<div class="sesion-header"><div class="sesion-avatar">' + ini + '</div><span class="sesion-dot"></span><span class="sesion-nombre">' + esc(s.nombre || s.usuario) + '</span></div><div class="sesion-rol">' + esc(rol) + ' - Conectado</div>' + suc;
        }
    } catch (e) {
        window.location.href = '/login';
        return;
    }
    await loadCatalogos();
    restaurarFiltros();
    loadDashboard();
    syncPedidosNuevos();
    setInterval(syncPedidosNuevos, 30000);
}

const FILTROS_VISTAS = [
    { vista: 'movimientos', inputs: ['mov-filtro', 'mov-desde', 'mov-hasta', 'mov-filtro-tipo'] },
    { vista: 'gastos', inputs: ['gasto-filtro', 'gasto-desde', 'gasto-hasta', 'gasto-sucursal-select'] },
    { vista: 'ventas', inputs: ['venta-filtro', 'venta-desde', 'venta-hasta', 'venta-sucursal-select'] },
    { vista: 'repartos', inputs: ['reparto-filtro', 'reparto-desde', 'reparto-hasta', 'reparto-sucursal-select'] },
    { vista: 'pedidos', inputs: ['pedido-filtro', 'pedido-estado'] },
    { vista: 'reportes', inputs: ['rep-desde', 'rep-hasta'] },
];
const KEY_FILTROS = 'pollos_filtros_diarios';

function hoyISO() {
    const d = new Date();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dia = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + dia;
}

function guardarFiltros() {
    const data = { fecha: hoyISO(), vistas: {} };
    FILTROS_VISTAS.forEach((v) => {
        const obj = {};
        v.inputs.forEach((id) => {
            const el = document.getElementById(id);
            if (el) obj[id] = el.value;
        });
        data.vistas[v.vista] = obj;
    });
    try { localStorage.setItem(KEY_FILTROS, JSON.stringify(data)); } catch (e) { }
}

function restaurarFiltros() {
    let data = null;
    try { data = JSON.parse(localStorage.getItem(KEY_FILTROS) || 'null'); } catch (e) { data = null; }
    const hoy = hoyISO();
    const esHoy = !!(data && data.fecha === hoy);
    FILTROS_VISTAS.forEach((v) => {
        v.inputs.forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            const guardado = esHoy && data.vistas && data.vistas[v.vista] && data.vistas[v.vista][id];
            if (guardado) {
                el.value = guardado;
            } else if (el.type === 'date') {
                el.value = hoy;
            } else {
                el.value = '';
            }
        });
    });
    if (!esHoy) {
        try { localStorage.removeItem(KEY_FILTROS); } catch (e) { }
    }
}

document.addEventListener('input', (e) => {
    if (e.target && e.target.matches && e.target.matches('input')) guardarFiltros();
});
document.addEventListener('change', (e) => {
    if (e.target && e.target.matches && e.target.matches('input, select')) guardarFiltros();
});
window.addEventListener('pagehide', guardarFiltros);

init();