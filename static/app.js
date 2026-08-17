const API = '/api';
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const on = (sel, ev, fn) => { const el = $(sel); if (el) el.addEventListener(ev, fn); };
const fmtNum = (n) => Number(n ?? 0).toLocaleString('es-PE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtDate = (d) => d ? d : '—';

let catalogos = { almacenes: [], categorias: [], proveedores: [] };

async function request(url, opts = {}) {
    const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
    });
    const json = await res.json();
    if (!json.ok) throw new Error(json.message || 'Error del servidor');
    return json.data;
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
    if (name === 'productos') loadProductos();
    if (name === 'movimientos') loadMovimientos();
    if (name === 'proveedores') loadProveedores();
    if (name === 'gastos') loadGastos();
    if (name === 'reportes') loadReportes();
    if (name === 'ventas') loadVentas();
    if (name === 'repartos') loadRepartos();
    if (name === 'fiados') loadFiados();
    if (name === 'usuarios') loadUsuarios();
    if (name === 'auditoria') loadAuditoria();
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
    fill('#prod-filtro-proveedor', catalogos.proveedores, 'Todos los proveedores');
    fill('#prod-almacen', catalogos.almacenes, '— Sin almacén —');
    fill('#mov-almacen', catalogos.almacenes, '— Sin almacén —');
    fill('#prod-proveedor', catalogos.proveedores, '— Sin proveedor —');
    fill('#gasto-proveedor', catalogos.proveedores, '— Sin proveedor —');
}

// ---------------- Dashboard ----------------
async function loadDashboard() {
    try {
        const d = await request(API + '/dashboard');
        $('#stat-productos').textContent = d.total_productos;
        $('#stat-stock').textContent = d.stock_total;
        $('#stat-valor').textContent = 'S/ ' + fmtNum(d.valor_inventario);
        $('#stat-ventas').textContent = 'S/ ' + fmtNum(d.ventas_mes);
        $('#stat-ventas-anio').textContent = 'S/ ' + fmtNum(d.ventas_anio);
        $('#stat-ventas-hoy').textContent = 'S/ ' + fmtNum(d.ventas_hoy);
        $('#stat-gastos').textContent = 'S/ ' + fmtNum(d.gastos_mes);
        $('#stat-gastos-anio').textContent = 'S/ ' + fmtNum(d.gastos_anio);
        $('#stat-repartos').textContent = d.repartos_mes || 0;
        $('#stat-utilidad').textContent = 'S/ ' + fmtNum(d.utilidad_mes);
        $('#stat-por-cobrar').textContent = 'S/ ' + fmtNum(d.por_cobrar);

        $('#stat-entradas-mes').textContent = d.entradas_mes;
        $('#stat-salidas-mes').textContent = d.salidas_mes;

        $('#dash-welcome').textContent = `Resumen del inventario · ${new Date().toLocaleDateString('es-PE', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}`;

        const t1 = $('#dash-movimientos');
        t1.innerHTML = d.mov_recientes.length ? d.mov_recientes.map((m) => `
            <tr>
                <td>${fmtDate(m.fecha)}</td>
                <td><strong>${m.producto_nombre}</strong></td>
                <td><span class="badge badge-${m.tipo}">${m.tipo === 'entrada' ? 'Entrada' : 'Salida'}</span></td>
                <td>${m.cantidad} ${m.unidad}</td>
                <td>${m.nota || ''}</td>
            </tr>`).join('')
            : '<tr><td colspan="5" class="empty">Sin movimientos registrados</td></tr>';

        const alertas = [];
        d.stock_bajo.forEach((p) => alertas.push(`
            <div class="alerta alerta-bajo">
                <div class="alerta-info"><strong>${p.nombre}</strong><span>Stock: ${p.stock} ${p.unidad} · Mínimo: ${p.stock_minimo} ${p.unidad}</span></div>
            </div>`));
        d.por_vencer.forEach((p) => alertas.push(`
            <div class="alerta alerta-vencer">
                <div class="alerta-info"><strong>${p.nombre}</strong><span>Vence el ${fmtDate(p.vencimiento)} · ${p.stock} ${p.unidad}</span></div>
            </div>`));
        $('#dash-alertas').innerHTML = alertas.length
            ? alertas.join('')
            : '<p class="empty">No hay alertas pendientes</p>';

        try {
            const g = await request(API + '/dashboard/graficos');
            graficoVentasGastos(g.meses);
            graficoUtilidad(g.meses);
            graficoHBar('#graf-top-productos', g.top_productos, 'cantidad', 'unid', '#FCC302');
            graficoHBar('#graf-top-repartos', g.top_repartos, 'total', 'S/ ', '#CF141D');
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
    const w = opt.w || 560, h = opt.h || 200;
    const padB = 30, padL = 46, padT = 12;
    const max = Math.max(...data.map((d) => getValor(d)), 1);
    const innerW = w - padL - 8, innerH = h - padB - padT;
    const n = data.length || 1;
    const slot = innerW / n;
    const barW = Math.min(34, slot * 0.55);
    let s = `<svg viewBox="0 0 ${w} ${h}">`;
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
    const w = 560, h = 200, padB = 30, padL = 46, padT = 12;
    const innerW = w - padL - 8, innerH = h - padB - padT;
    const n = meses.length || 1;
    const slot = innerW / n;
    const barW = Math.min(16, slot / 3.2);
    let s = `<svg viewBox="0 0 ${w} ${h}">`;
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
        s += `<rect x="${cx - barW - 1.5}" y="${yv}" width="${barW}" height="${Math.max(vh, 1)}" rx="3" fill="#FCC302"><title>Ventas: S/ ${m.ventas}</title></rect>`;
        s += `<rect x="${cx + 1.5}" y="${yg}" width="${barW}" height="${Math.max(gh, 1)}" rx="3" fill="#CF141D"><title>Gastos: S/ ${m.gastos}</title></rect>`;
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
        const prods = await request(API + '/productos?' + qs.toString());
        $('#productos-tbody').innerHTML = prods.map((p) => `
            <tr>
                <td>${p.codigo || '—'}</td>
                <td><strong>${p.nombre}</strong></td>
                <td>${p.categoria_nombre || '—'}</td>
                <td>${p.almacen_nombre || '—'}</td>
                <td>${p.stock} ${p.unidad}</td>
                <td>${p.stock_minimo} ${p.unidad}</td>
                <td>${p.unidad}</td>
                <td>S/ ${fmtNum(p.costo_promedio)}</td>
                <td>${fmtDate(p.vencimiento)}</td>
                <td>
                    <button class="btn btn-icon" data-edit-prod="${p.id}" title="Editar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg></button>
                    <button class="btn btn-icon btn-danger" data-del-prod="${p.id}" title="Eliminar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
                </td>
            </tr>`).join('') || '<tr><td colspan="10" class="empty">No hay productos</td></tr>';

        $$('[data-edit-prod]').forEach((b) => b.addEventListener('click', () => openProductoModal(Number(b.dataset.editProd), prods)));
        $$('[data-del-prod]').forEach((b) => b.addEventListener('click', () => delProducto(b.dataset.delProd)));
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#prod-filtro').addEventListener('input', debounce(loadProductos, 300));
$('#prod-categoria').addEventListener('change', loadProductos);
on('#prod-filtro-proveedor', 'change', loadProductos);
on('#prod-estado', 'change', loadProductos);
$('#btn-nuevo-producto').addEventListener('click', () => openProductoModal());

async function openProductoModal(id, lista) {
    await loadCatalogos();
    const form = $('#form-producto');
    form.reset();
    $('#prod-id').value = '';
    $('#modal-producto-title').textContent = 'Nuevo producto';
    $('#campo-stock-inicial').style.display = 'flex';

    if (id) {
        const p = lista.find((x) => x.id === id);
        $('#modal-producto-title').textContent = 'Editar producto';
        $('#prod-id').value = p.id;
        $('#prod-codigo').value = p.codigo || '';
        $('#prod-nombre').value = p.nombre;
        $('#prod-categoria-form').value = p.categoria_id || '';
        $('#prod-almacen').value = p.almacen_id || '';
        $('#prod-unidad').value = p.unidad || 'unidad';
        $('#prod-minimo').value = p.stock_minimo;
        $('#prod-costo').value = p.costo_promedio;
        $('#prod-precio-venta').value = p.precio_venta;
        $('#prod-vencimiento').value = p.vencimiento || '';
        $('#prod-proveedor').value = p.proveedor_id || '';
        $('#campo-stock-inicial').style.display = 'none';
    }
    $('#modal-producto').classList.add('open');
}

$('#form-producto').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = $('#prod-id').value;
    const body = {
        codigo: $('#prod-codigo').value.trim() || null,
        nombre: $('#prod-nombre').value,
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
    try {
        if (id) {
            await request(API + '/productos/' + id, { method: 'PUT', body: JSON.stringify(body) });
        } else {
            await request(API + '/productos', { method: 'POST', body: JSON.stringify(body) });
        }
        toast(id ? 'Producto actualizado' : 'Producto creado');
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

// ---------------- Movimientos ----------------
async function loadMovimientos() {
    try {
        const prods = await request(API + '/productos');
        $('#mov-producto').innerHTML = '<option value="">Seleccione...</option>' +
            prods.map((p) => `<option value="${p.id}">${p.nombre} (${p.stock} ${p.unidad})</option>`).join('');
        await listarMovimientos();
    } catch (e) {
        toast(e.message, 'err');
    }
}

async function listarMovimientos() {
    const qs = new URLSearchParams();
    const filtro = ($('#mov-filtro') || {}).value?.trim() || '';
    const desde = ($('#mov-desde') || {}).value || '', hasta = ($('#mov-hasta') || {}).value || '';
    if (filtro) qs.set('filtro', filtro);
    if (desde) qs.set('desde', desde);
    if (hasta) qs.set('hasta', hasta);
    if ($('#mov-filtro-tipo').value) qs.set('tipo', $('#mov-filtro-tipo').value);
    const movs = await request(API + '/movimientos?' + qs.toString());
    $('#movimientos-tbody').innerHTML = movs.map((m) => `
        <tr>
            <td>${fmtDate(m.fecha)}</td>
            <td>${m.producto_nombre}</td>
            <td><span class="badge badge-${m.tipo}">${m.tipo === 'entrada' ? 'Entrada' : 'Salida'}</span></td>
            <td>${m.cantidad} ${m.unidad}</td>
            <td>S/ ${fmtNum(m.precio_unitario)}</td>
            <td>${m.almacen_nombre || '—'}</td>
            <td>${m.nota || ''}</td>
        </tr>`).join('') || '<tr><td colspan="7" class="empty">Sin movimientos</td></tr>';
}

$('#form-movimiento').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {
        producto_id: +$('#mov-producto').value,
        tipo: $('#mov-tipo').value,
        cantidad: +$('#mov-cantidad').value,
        precio_unitario: +$('#mov-precio').value || 0,
        fecha: $('#mov-fecha').value || undefined,
        almacen_id: +$('#mov-almacen').value || null,
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

$('#btn-filtrar-mov').addEventListener('click', listarMovimientos);
on('#mov-filtro', 'input', debounce(listarMovimientos, 300));
vincularEscaneo('#mov-escaneo', '#mov-producto', '#mov-cantidad');
$('#mov-tipo').addEventListener('change', () => {
    const label = $('#mov-precio');
    $('#mov-tipo').value === 'salida'
        ? label.closest('label').style.display = 'none'
        : label.closest('label').style.display = 'flex';
});

// ---------------- Proveedores ----------------
async function loadProveedores() {
    try {
        const qs = new URLSearchParams();
        const filtro = ($('#proveedor-filtro') || {}).value?.trim() || '';
        if (filtro) qs.set('filtro', filtro);
        const provs = await request(API + '/proveedores?' + qs.toString());
        $('#proveedores-tbody').innerHTML = provs.map((p) => `
            <tr>
                <td><strong>${p.nombre}</strong></td>
                <td>${p.telefono || '—'}</td>
                <td>${p.email || '—'}</td>
                <td>${p.direccion || '—'}</td>
                <td>
                    <button class="btn btn-icon" data-edit-prov="${p.id}" title="Editar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg></button>
                    <button class="btn btn-icon btn-danger" data-del-prov="${p.id}" title="Eliminar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
                </td>
            </tr>`).join('') || '<tr><td colspan="5" class="empty">Sin proveedores</td></tr>';

        $$('[data-edit-prov]').forEach((b) => b.addEventListener('click', () => openProveedorModal(Number(b.dataset.editProv), provs)));
        $$('[data-del-prov]').forEach((b) => b.addEventListener('click', () => delProveedor(b.dataset.delProv)));
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#btn-nuevo-proveedor').addEventListener('click', () => openProveedorModal());
on('#proveedor-filtro', 'input', debounce(loadProveedores, 300));

function openProveedorModal(id, lista) {
    const form = $('#form-proveedor');
    form.reset();
    $('#prov-id').value = '';
    if (id) {
        const p = lista.find((x) => x.id === id);
        $('#prov-id').value = p.id;
        $('#prov-nombre').value = p.nombre;
        $('#prov-telefono').value = p.telefono;
        $('#prov-email').value = p.email;
        $('#prov-direccion').value = p.direccion;
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
        const qs = new URLSearchParams();
        const filtro = ($('#gasto-filtro') || {}).value?.trim() || '';
        if (filtro) qs.set('filtro', filtro);
        if (($('#gasto-desde') || {}).value) qs.set('desde', $('#gasto-desde').value);
        if ($('#gasto-hasta').value) qs.set('hasta', $('#gasto-hasta').value);
        const gastos = await request(API + '/gastos?' + qs.toString());
        $('#gastos-tbody').innerHTML = gastos.map((g) => `
            <tr>
                <td>${fmtDate(g.fecha)}</td>
                <td>${g.categoria}</td>
                <td>${g.descripcion || '—'}</td>
                <td><strong>S/ ${fmtNum(g.monto)}</strong></td>
                <td><button class="btn btn-icon btn-danger" data-del-gasto="${g.id}" title="Eliminar"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button></td>
            </tr>`).join('') || '<tr><td colspan="5" class="empty">Sin gastos registrados</td></tr>';
        $$('[data-del-gasto]').forEach((b) => b.addEventListener('click', () => delGasto(b.dataset.delGasto)));
    } catch (e) {
        toast(e.message, 'err');
    }
}

$('#form-gasto').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {
        categoria: $('#gasto-categoria').value,
        descripcion: $('#gasto-descripcion').value,
        monto: +$('#gasto-monto').value,
        fecha: $('#gasto-fecha').value || undefined,
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

$('#btn-filtrar-gastos').addEventListener('click', loadGastos);
on('#gasto-filtro', 'input', debounce(loadGastos, 300));

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
                <td>S/ ${fmtNum(c.total)}</td>
            </tr>`).join('') || '<tr><td colspan="4" class="empty">Sin datos</td></tr>';

        const valorizacion = await request(API + '/reportes/valorizacion');
        $('#rep-valorizacion').innerHTML = valorizacion.map((v) => `
            <tr>
                <td>${v.nombre}</td><td>${v.stock} ${v.unidad}</td>
                <td>S/ ${fmtNum(v.costo_promedio)}</td><td><strong>S/ ${fmtNum(v.valor)}</strong></td>
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
                <td><strong>S/ ${fmtNum(r.total_repartido)}</strong></td>
            </tr>`).join('')
            || '<tr><td colspan="4" class="empty">Sin datos</td></tr>';

        const ganancias = await request(API + '/reportes/ganancias?' + qs.toString());
        let totVenta = 0, totCosto = 0, totUtil = 0;
        $('#rep-ganancias').innerHTML = ganancias.map((g) => {
            totVenta += g.venta; totCosto += g.costo; totUtil += g.utilidad;
            return `<tr>
                <td><strong>${g.nombre}</strong></td>
                <td>${g.cantidad}</td>
                <td>S/ ${fmtNum(g.venta)}</td>
                <td>S/ ${fmtNum(g.costo)}</td>
                <td><span class="${g.utilidad < 0 ? 'text-red' : ''}"><strong>S/ ${fmtNum(g.utilidad)}</strong></span></td>
            </tr>`;
        }).join('') || '<tr><td colspan="5" class="empty">Sin ventas en el período</td></tr>';
        if (ganancias.length) {
            $('#rep-ganancias').innerHTML += `
                <tr class="total-row">
                    <td><strong>TOTAL</strong></td><td></td>
                    <td><strong>S/ ${fmtNum(totVenta)}</strong></td>
                    <td><strong>S/ ${fmtNum(totCosto)}</strong></td>
                    <td><strong>S/ ${fmtNum(totUtil)}</strong></td>
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
        const prods = await request(API + '/productos');
        $('#venta-producto').innerHTML = '<option value="">Seleccione producto...</option>' +
            prods.map((p) => `<option value="${p.id}" data-precio="${p.precio_venta || ''}" data-stock="${p.stock}">${p.nombre} (stock: ${p.stock} ${p.unidad})</option>`).join('');
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
            <td>S/ ${fmtNum(it.precio)}</td>
            <td>S/ ${fmtNum(it.cantidad * it.precio)}</td>
            <td><button class="btn btn-icon btn-danger" onclick="quitarItemVenta(${i})">Quitar</button></td>
        </tr>`).join('')
        : '<tr><td colspan="5" class="empty">Agrega productos a la venta</td></tr>';
    const total = ventaItems.reduce((s, it) => s + it.cantidad * it.precio, 0);
    $('#venta-total').textContent = 'S/ ' + fmtNum(total);
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

$('#venta-fiado').addEventListener('change', (e) => {
    $('#campo-fiado').style.display = e.target.checked ? 'block' : 'none';
});

$('#form-venta').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!ventaItems.length) return toast('La venta no tiene productos', 'err');
    try {
        const res = await request(API + '/ventas', {
            method: 'POST',
            body: JSON.stringify({
                fecha: $('#venta-fecha').value || undefined,
                nota: $('#venta-nota').value,
                fiado: $('#venta-fiado').checked ? 1 : 0,
                cliente: $('#venta-cliente').value.trim(),
                telefono: $('#venta-telefono').value.trim(),
                detalle: ventaItems,
            }),
        });
        toast('Venta registrada por S/ ' + fmtNum(res.total));
        ventaItems = [];
        actVentaItems();
        e.target.reset();
        $('#venta-fecha').value = new Date().toISOString().slice(0, 10);
        listarVentas();
    } catch (err) {
        toast(err.message, 'err');
    }
});

async function listarVentas() {
    const qs = new URLSearchParams();
    const filtro = ($('#venta-filtro') || {}).value?.trim() || '';
    if (filtro) qs.set('filtro', filtro);
    if (($('#venta-desde') || {}).value) qs.set('desde', $('#venta-desde').value);
    if ($('#venta-hasta').value) qs.set('hasta', $('#venta-hasta').value);
    const ventas = await request(API + '/ventas?' + qs.toString());
    $('#ventas-tbody').innerHTML = ventas.map((v) => `
        <tr>
            <td>#${v.id}</td>
            <td>${fmtDate(v.fecha)}</td>
            <td>${v.num_items} items</td>
            <td><strong>S/ ${fmtNum(v.total)}</strong></td>
            <td>${v.usuario || '—'}</td>
            <td>
                <button class="btn btn-icon" onclick="verVenta(${v.id})" title="Ver detalle"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
                <button class="btn btn-icon btn-danger" onclick="anularVenta(${v.id})" title="Anular venta"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z"/></svg></button>
            </td>
        </tr>`).join('') || '<tr><td colspan="7" class="empty">Sin ventas registradas</td></tr>';
}

window.anularVenta = async (id) => {
    if (!confirm('¿Anular la venta #' + id + '? Se repondrá el stock y se cancelará su fiado (si no tiene pagos).')) return;
    try {
        const res = await request(API + '/ventas/' + id, { method: 'DELETE' });
        toast(res.message, 'ok');
        listarVentas();
        loadDashboard();
        loadFiados();
    } catch (e) {
        toast(e.message, 'err');
    }
};

window.verVenta = async (id) => {
    try {
        const data = await request(API + '/ventas/' + id);
        const v = data.venta;
        const det = data.detalle.map((d) => `<tr><td>${d.producto_nombre}</td><td>${d.cantidad}</td><td>S/ ${fmtNum(d.precio_unitario)}</td><td>S/ ${fmtNum(d.subtotal)}</td></tr>`).join('');
        alert('Venta #' + v.id + ' · ' + v.fecha + '\n\n' + det + '\n\nTOTAL: S/ ' + fmtNum(v.total));
    } catch (e) {
        toast(e.message, 'err');
    }
};

$('#btn-filtrar-ventas').addEventListener('click', listarVentas);
on('#venta-filtro', 'input', debounce(listarVentas, 300));
$('#btn-exportar-ventas').addEventListener('click', () => {
    window.location.href = '/api/exportar/ventas?desde=' + $('#venta-desde').value + '&hasta=' + $('#venta-hasta').value;
});
vincularEscaneo('#venta-escaneo', '#venta-producto', '#venta-cantidad');

// ---------------- Repartos ----------------
let repartoItems = [];

async function loadRepartos() {
    try {
        await loadCatalogos();
        const prods = await request(API + '/productos');
        $('#reparto-producto').innerHTML = '<option value="">Seleccione producto...</option>' +
            prods.map((p) => `<option value="${p.id}" data-costo="${p.costo_promedio || ''}" data-stock="${p.stock}">${p.nombre} (stock: ${p.stock} ${p.unidad})</option>`).join('');
        const sucursales = await request(API + '/sucursales');
        $('#reparto-sucursal').innerHTML = sucursales.map((s) =>
            `<option value="${s.id}">${s.principal ? '★ ' : ''}${s.nombre}</option>`).join('');
        $('#repartos-info').textContent = `Cochabamba · ${sucursales.length} sucursales`;
        if (window.ROL === 'admin') {
            $('#btn-gestionar-sucursales').style.display = 'inline-flex';
        }
        await listarRepartos();
    } catch (e) {
        toast(e.message, 'err');
    }
}

function actRepartoItems() {
    $('#reparto-items-tbody').innerHTML = repartoItems.length ? repartoItems.map((it, i) => `
        <tr>
            <td><strong>${it.nombre}</strong></td>
            <td>${it.cantidad}</td>
            <td>S/ ${fmtNum(it.costo)}</td>
            <td>S/ ${fmtNum(it.cantidad * it.costo)}</td>
            <td><button class="btn btn-icon btn-danger" onclick="quitarItemReparto(${i})">Quitar</button></td>
        </tr>`).join('')
        : '<tr><td colspan="5" class="empty">Agrega productos al reparto</td></tr>';
    const total = repartoItems.reduce((s, it) => s + it.cantidad * it.costo, 0);
    $('#reparto-total').textContent = 'S/ ' + fmtNum(total);
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
                fecha: $('#reparto-fecha').value || undefined,
                sucursal_id: +$('#reparto-sucursal').value,
                nota: $('#reparto-nota').value,
                detalle: repartoItems,
            }),
        });
        toast('Reparto registrado por S/ ' + fmtNum(res.total));
        repartoItems = [];
        actRepartoItems();
        e.target.reset();
$('#reparto-fecha').value = new Date().toISOString().slice(0, 10);
$('#pago-fecha').value = new Date().toISOString().slice(0, 10);
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
    const repartos = await request(API + '/repartos?' + qs.toString());
    $('#repartos-tbody').innerHTML = repartos.map((r) => `
        <tr>
            <td>#${r.id}</td>
            <td>${fmtDate(r.fecha)}</td>
            <td><strong>${r.sucursal_nombre}</strong></td>
            <td>${r.num_items} items</td>
            <td><strong>S/ ${fmtNum(r.total)}</strong></td>
            <td>${r.usuario || '—'}</td>
            <td>
                <button class="btn btn-icon" onclick="verReparto(${r.id})" title="Ver detalle"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
                <button class="btn btn-icon btn-danger" onclick="anularReparto(${r.id})" title="Anular reparto"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z"/></svg></button>
            </td>
        </tr>`).join('') || '<tr><td colspan="8" class="empty">Sin repartos registrados</td></tr>';
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
        const det = data.detalle.map((d) => `<tr><td>${d.producto_nombre}</td><td>${d.cantidad}</td><td>S/ ${fmtNum(d.costo_unitario)}</td><td>S/ ${fmtNum(d.subtotal)}</td></tr>`).join('');
        alert('Reparto #' + r.id + ' · ' + r.fecha + ' · ' + r.sucursal_nombre + '\n\n' + det + '\n\nTOTAL: S/ ' + fmtNum(r.total));
    } catch (e) {
        toast(e.message, 'err');
    }
};

$('#btn-filtrar-repartos').addEventListener('click', listarRepartos);
on('#reparto-filtro', 'input', debounce(listarRepartos, 300));
$('#btn-exportar-repartos').addEventListener('click', () => {
    window.location.href = '/api/exportar/repartos?desde=' + $('#reparto-desde').value + '&hasta=' + $('#reparto-hasta').value;
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
            <td>S/ ${fmtNum(s.total_repartido)}</td>
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

// ---------------- Fiados / cuentas por cobrar ----------------
async function loadFiados() {
    try {
        const qs = new URLSearchParams();
        const filtro = ($('#fiado-filtro') || {}).value?.trim() || '';
        if (filtro) qs.set('filtro', filtro);
        const data = await request(API + '/creditos?' + qs.toString());
        $('#fiado-por-cobrar').textContent = 'S/ ' + fmtNum(data.total_pendiente);
        $('#fiado-cobrado').textContent = 'S/ ' + fmtNum(data.total_cobrado);

        const pendientes = data.lista.filter((c) => c.estado === 'pendiente');
        const pagadas = data.lista.filter((c) => c.estado === 'pagado');

        $('#fiados-tbody').innerHTML = pendientes.length ? pendientes.map((c) => `
            <tr>
                <td>${fmtDate(c.fecha)}</td>
                <td><strong>${c.cliente}</strong></td>
                <td>${c.telefono || '—'}</td>
                <td>S/ ${fmtNum(c.monto)}</td>
                <td><strong class="text-red">S/ ${fmtNum(c.saldo)}</strong></td>
                <td>${c.usuario || '—'}</td>
                <td>
                    <button class="btn btn-icon" onclick="abrirPago(${c.id}, '${c.cliente.replace(/'/g, "\\'")}', ${c.saldo})" title="Registrar pago">Pagar</button>
                    <button class="btn btn-icon" onclick="pagarCompleto(${c.id})" title="Marcar como pagado completo">Pagar todo</button>
                </td>
            </tr>`).join('')
            : '<tr><td colspan="7" class="empty">No hay ventas al fiado pendientes</td></tr>';

        $('#fiados-pagados-tbody').innerHTML = pagadas.length ? pagadas.map((c) => `
            <tr>
                <td>${fmtDate(c.fecha)}</td>
                <td><strong>${c.cliente}</strong></td>
                <td>S/ ${fmtNum(c.monto)}</td>
                <td><span class="badge badge-entrada">Pagado</span></td>
                <td>${c.usuario || '—'}</td>
            </tr>`).join('')
            : '<tr><td colspan="5" class="empty">Aún no hay fiados pagados</td></tr>';
    } catch (e) {
        toast(e.message, 'err');
    }
}

window.abrirPago = (id, cliente, saldo) => {
    $('#pago-credito-id').value = id;
    $('#pago-monto').value = '';
    $('#pago-monto').max = saldo;
    $('#pago-info').textContent = `Cliente: ${cliente} · Saldo pendiente: S/ ${fmtNum(saldo)}`;
    $('#modal-pago').classList.add('open');
    $('#pago-monto').focus();
};

window.pagarCompleto = async (id) => {
    if (!confirm('¿Marcar este fiado como totalmente pagado?')) return;
    try {
        await request(API + '/creditos/' + id + '/pago', {
            method: 'POST',
            body: JSON.stringify({ monto: 999999999 }),
        });
        toast('Pago registrado');
        loadFiados();
    } catch (e) {
        toast(e.message, 'err');
    }
};

$('#form-pago').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = $('#pago-credito-id').value;
    try {
        const res = await request(API + '/creditos/' + id + '/pago', {
            method: 'POST',
            body: JSON.stringify({
                monto: +$('#pago-monto').value,
                fecha: $('#pago-fecha').value || undefined,
            }),
        });
        toast('Pago registrado' + (res.estado === 'pagado' ? ' · fiado saldado' : ''));
        $('#modal-pago').classList.remove('open');
        loadFiados();
    } catch (err) {
        toast(err.message, 'err');
    }
});

$('#btn-refrescar-fiados').addEventListener('click', loadFiados);
on('#fiado-filtro', 'input', debounce(loadFiados, 300));

// ---------------- Usuarios ----------------
async function loadUsuarios() {
    try {
        const users = await request(API + '/usuarios');
        $('#usuarios-tbody').innerHTML = users.map((u) => `
            <tr>
                <td><strong>${u.usuario}</strong></td>
                <td>${u.nombre || '—'}</td>
                <td><span class="badge ${u.rol === 'admin' ? 'badge-bajo' : 'badge-entrada'}">${u.rol === 'admin' ? 'Administrador' : 'Encargado'}</span></td>
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
            $('#user-rol').value = u.rol;
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

// ---------------- Contraseña y sesión ----------------
$('#btn-cambiar-pass').addEventListener('click', () => {
    $('#form-password').reset();
    $('#modal-password').classList.add('open');
});

$('#form-password').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        await request(API + '/cambiar_password', {
            method: 'POST',
            body: JSON.stringify({
                actual: $('#pass-actual').value,
                nueva: $('#pass-nueva').value,
            }),
        });
        toast('Contraseña actualizada');
        $('#modal-password').classList.remove('open');
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
$('#mov-fecha').value = new Date().toISOString().slice(0, 10);
$('#gasto-fecha').value = new Date().toISOString().slice(0, 10);
$('#venta-fecha').value = new Date().toISOString().slice(0, 10);
$('#reparto-fecha').value = new Date().toISOString().slice(0, 10);

// ---------------- Inicio ----------------
(async function init() {
    try {
        const s = await request(API + '/sesion');
        window.ROL = s.rol;
        if (s.rol !== 'admin') {
            ['usuarios', 'auditoria', 'respaldo'].forEach((v) => {
                const btn = document.querySelector(`.menu-btn[data-view="${v}"]`);
                if (btn) btn.style.display = 'none';
            });
        }
    } catch (e) {
        window.location.href = '/login';
        return;
    }
    await loadCatalogos();
    loadDashboard();
})();
