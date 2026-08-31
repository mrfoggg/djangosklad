function parseRetailMoney(value) {
	const normalized = String(value ?? "")
		.replace(/\s/g, "")
		.replace(",", ".");
	const number = Number.parseFloat(normalized);
	return Number.isFinite(number) ? number : null;
}

function formatRetailMetric(value) {
	return Number.isFinite(value) ? `${value.toFixed(1)}%` : "—";
}

function escapeRetailHtml(value) {
	const element = document.createElement("span");
	element.textContent = String(value ?? "");
	return element.innerHTML;
}

function getRetailPriceInput(row) {
	return (
		row?.querySelector('input[id$="-price_0"]') ||
		row?.querySelector('input[id$="-price"]')
	);
}

function updateRetailPriceMetrics(row) {
	const info = row?.querySelector(".supplier-price-info");
	const priceInput = getRetailPriceInput(row);
	if (!info || !priceInput) return;

	const salePrice = parseRetailMoney(priceInput.value);
	const purchasePrice = parseRetailMoney(info.dataset.purchasePrice);
	const rrp = parseRetailMoney(info.dataset.rrp);
	const markup =
		salePrice !== null && purchasePrice !== null && purchasePrice > 0
			? ((salePrice - purchasePrice) / purchasePrice) * 100
			: null;
	const margin =
		salePrice !== null && purchasePrice !== null && salePrice > 0
			? ((salePrice - purchasePrice) / salePrice) * 100
			: null;

	const supplier = escapeRetailHtml(info.dataset.supplier || "Основной поставщик");
	const purchaseText = purchasePrice !== null ? purchasePrice.toFixed(2) : "—";
	const rrpText = rrp !== null ? rrp.toFixed(2) : "—";
	info.innerHTML =
		`<strong>${supplier}</strong><br>` +
		`РРЦ: <strong>${rrpText}</strong> · Закупка: <strong>${purchaseText}</strong><br>` +
		`Наценка: <strong>${formatRetailMetric(markup)}</strong> / ` +
		`Маржа: <strong>${formatRetailMetric(margin)}</strong>`;

	const isBelowRrp =
		salePrice !== null && rrp !== null && rrp > 0 && salePrice < rrp;
	priceInput.style.backgroundColor = isBelowRrp ? "#7f1d1d" : "";
	priceInput.style.color = isBelowRrp ? "#ffffff" : "";
}

async function fetchMainSupplierPrice(productSelect) {
	const row = productSelect.closest("tr, .inline-related");
	const info = row?.querySelector(".supplier-price-info");
	if (!info) return;

	if (!productSelect.value) {
		info.dataset.purchasePrice = "";
		info.dataset.rrp = "";
		info.dataset.supplier = "";
		info.textContent = "Выберите товар";
		const priceInput = getRetailPriceInput(row);
		if (priceInput) {
			priceInput.style.backgroundColor = "";
			priceInput.style.color = "";
		}
		return;
	}

	info.textContent = "Загрузка прайса…";
	const url = new URL("/documents/get-price/", window.location.origin);
	url.searchParams.set("product_id", productSelect.value);
	url.searchParams.set("use_main_supplier", "1");
	const organizationId = document.getElementById("id_organization")?.value;
	if (organizationId) url.searchParams.set("organization_id", organizationId);

	try {
		const response = await fetch(url);
		const data = await response.json();
		if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);

		info.dataset.purchasePrice = data.status === "success" ? data.price : "";
		info.dataset.rrp = data.status === "success" ? data.rrp : "";
		info.dataset.supplier = data.supplier || "";
		if (data.status === "success") {
			updateRetailPriceMetrics(row);
		} else {
			info.textContent = data.message || data.title || "Прайс не найден";
		}
	} catch (error) {
		console.error("Supplier price fetch error:", error);
		info.textContent = "Не удалось загрузить прайс поставщика";
	}
}

document.addEventListener("change", (event) => {
	if (event.target?.matches('select[id$="-product"]')) {
		fetchMainSupplierPrice(event.target);
	} else if (event.target?.id === "id_organization") {
		document
			.querySelectorAll('select[id$="-product"]')
			.forEach((productSelect) => fetchMainSupplierPrice(productSelect));
	} else if (event.target === getRetailPriceInput(event.target?.closest("tr, .inline-related"))) {
		updateRetailPriceMetrics(event.target.closest("tr, .inline-related"));
	}
});

document.addEventListener("input", (event) => {
	const row = event.target?.closest("tr, .inline-related");
	if (event.target === getRetailPriceInput(row)) updateRetailPriceMetrics(row);
});

function initializeRetailPriceInfo() {
	document.querySelectorAll('select[id$="-product"]').forEach((productSelect) => {
		if (productSelect.value) fetchMainSupplierPrice(productSelect);
	});
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", initializeRetailPriceInfo);
} else {
	initializeRetailPriceInfo();
}
