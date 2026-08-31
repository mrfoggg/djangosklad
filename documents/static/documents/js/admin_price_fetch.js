function showPriceNotification(data) {
	const isDark = document.documentElement.classList.contains("dark");

	Swal.fire({
		icon: data.status,
		title: data.title,
		html: data.message,
		toast: true,
		position: "top-end",
		showConfirmButton: false,
		timer: 4000,
		timerProgressBar: true,
		background: isDark ? "#1f2937" : "#fff",
		color: isDark ? "#fff" : "#000",
	});
}

function updateCustomerOrderPriceHighlight(row) {
	if (!document.getElementById("id_customer")) {
		return;
	}

	const priceInput = row?.querySelector('input[id$="-customer_price"]');
	const rrpInput = row?.querySelector('input[id$="-rrp"]');
	if (!priceInput || !rrpInput) {
		return;
	}

	const price = Number.parseFloat(priceInput.value.replace(",", "."));
	const rrp = Number.parseFloat(rrpInput.value.replace(",", "."));
	const isBelowRrp = Number.isFinite(price) && Number.isFinite(rrp) && rrp > 0 && price < rrp;

	priceInput.classList.remove(
		"bg-red-100",
		"dark:bg-red-900/30",
		"!bg-red-100",
		"dark:!bg-red-900/30",
	);
	priceInput.style.backgroundColor = isBelowRrp ? "#7f1d1d" : "";
}

function getPriceSource() {
	if (document.getElementById("id_customer")) {
		return {
			endpoint: "/documents/get-retail-price/",
			id: document.getElementById("id_retail_store")?.value,
			idParameter: "retail_store_id",
			isRetail: true,
		};
	}

	return {
		endpoint: "/documents/get-price/",
		id: document.getElementById("id_supplier")?.value,
		idParameter: "supplier_id",
		isRetail: false,
	};
}

async function updateLatestPrice(productSelect, { showNotification = true } = {}) {
	const productId = productSelect.value;
	const priceSource = getPriceSource();
	const row = productSelect.closest("tr, .inline-related");
	const priceFieldSuffix = priceSource.isRetail ? "-customer_price" : "-purchase_price";
	const priceInput = row?.querySelector(`input[id$="${priceFieldSuffix}"]`);
	const rrpInput = row?.querySelector('input[id$="-rrp"]');

	if ((!priceSource.isRetail && !priceSource.id) || !productId || !priceInput) {
		return { status: "skipped" };
	}

	const rowOrganizationSelect = row.querySelector('select[id$="-organization"]');
	const documentOrganizationSelect = document.getElementById("id_organization");
	const organizationId =
		rowOrganizationSelect?.value || documentOrganizationSelect?.value || null;
	const priceType = document.getElementById("id_price_type")?.value;

	const url = new URL(priceSource.endpoint, window.location.origin);
	if (priceSource.id) {
		url.searchParams.append(priceSource.idParameter, priceSource.id);
	}
	url.searchParams.append("product_id", productId);
	if (!priceSource.isRetail && organizationId) {
		url.searchParams.append("organization_id", organizationId);
	}
	if (!priceSource.isRetail && priceType) {
		url.searchParams.append("price_type", priceType);
	}

	try {
		const response = await fetch(url);
		const data = await response.json();

		priceInput.value = data.price;
		if (rrpInput && data.rrp !== undefined) {
			rrpInput.value = data.rrp;
			rrpInput.dispatchEvent(new Event("change", { bubbles: true }));
		}
		const colorClass =
			data.status === "success"
				? "bg-green-100"
				: data.status === "error"
					? "bg-red-100"
					: "bg-yellow-100";
		const darkColorClass =
			data.status === "success"
				? "dark:bg-green-900/30"
				: data.status === "error"
					? "dark:bg-red-900/30"
					: "dark:bg-yellow-900/30";

		priceInput.classList.add(colorClass, darkColorClass);
		setTimeout(() => priceInput.classList.remove(colorClass, darkColorClass), 1000);
		priceInput.dispatchEvent(new Event("change", { bubbles: true }));

		if (showNotification) {
			showPriceNotification(data);
		}

		return { status: data.status };
	} catch (error) {
		console.error("Fetch error:", error);
		return { status: "request_failed" };
	}
}

async function updatePurchasePriceFromOrder(productSelect, { showNotification = true } = {}) {
	if (!getPriceSource().isRetail) {
		return { status: "skipped" };
	}

	const row = productSelect.closest("tr, .inline-related");
	const purchaseOrderSelect = row?.querySelector('select[id$="-purchase_order"]');
	const purchasePriceInput = row?.querySelector('input[id$="-purchase_price"]');
	const rrpInput = row?.querySelector('input[id$="-rrp"]');
	if (!productSelect.value || !purchaseOrderSelect?.value || !purchasePriceInput) {
		return { status: "skipped" };
	}

	const url = new URL("/documents/get-price/", window.location.origin);
	url.searchParams.append("product_id", productSelect.value);
	url.searchParams.append("purchase_order_id", purchaseOrderSelect.value);

	try {
		const response = await fetch(url);
		const data = await response.json();

		purchasePriceInput.value = data.price;
		purchasePriceInput.dispatchEvent(new Event("change", { bubbles: true }));
		if (rrpInput && data.rrp !== undefined) {
			rrpInput.value = data.rrp;
			rrpInput.dispatchEvent(new Event("change", { bubbles: true }));
		}

		if (showNotification) {
			showPriceNotification(data);
		}

		return { status: data.status };
	} catch (error) {
		console.error("Fetch error:", error);
		return { status: "request_failed" };
	}
}

document.addEventListener("change", async (event) => {
	const priceSource = getPriceSource();
	const isSupplierChanged = event.target?.id === "id_supplier";
	const isRetailStoreChanged = event.target?.id === "id_retail_store";
	const isDocumentOrganizationChanged = event.target?.id === "id_organization";
	const isPriceTypeChanged = event.target?.id === "id_price_type";
	const isRowOrganizationChanged =
		event.target?.id.endsWith("-organization") && !isDocumentOrganizationChanged;
	const shouldBatchUpdate =
		(!priceSource.isRetail &&
			(isSupplierChanged || isDocumentOrganizationChanged || isPriceTypeChanged)) ||
		(priceSource.isRetail && isRetailStoreChanged);

	if (shouldBatchUpdate) {
		if (!priceSource.id || (isSupplierChanged && !event.target.value)) {
			return;
		}

		const productSelects = [...document.querySelectorAll('select[id$="-product"]')].filter(
			(productSelect) => {
				if (!productSelect.value) {
					return false;
				}

				if (!isDocumentOrganizationChanged || priceSource.isRetail) {
					return true;
				}

				const row = productSelect.closest("tr, .inline-related");
				return !row?.querySelector('select[id$="-organization"]')?.value;
			},
		);
		const results = await Promise.all(
			productSelects.map((productSelect) =>
				updateLatestPrice(productSelect, { showNotification: false }),
			),
		);
		const foundCount = results.filter(({ status }) => status === "success").length;
		const notFoundCount = results.filter(({ status }) =>
			["info", "error"].includes(status),
		).length;
		const failedCount = results.filter(({ status }) => status === "request_failed").length;

		showPriceNotification({
			status: failedCount ? "warning" : notFoundCount ? "info" : "success",
			title: "Цены обновлены",
			message:
				`Найдено цен: <strong>${foundCount}</strong>.<br>` +
				`Не найдено: <strong>${notFoundCount}</strong> — установлено 0.` +
				(failedCount ? `<br>Не удалось обновить: <strong>${failedCount}</strong>.` : ""),
		});
		return;
	}

	if (event.target?.id.endsWith("-product")) {
		await updateLatestPrice(event.target);
		const row = event.target.closest("tr, .inline-related");
		if (getPriceSource().isRetail && row?.querySelector('select[id$="-purchase_order"]')?.value) {
			await updatePurchasePriceFromOrder(event.target, { showNotification: false });
		}
	} else if (isRowOrganizationChanged) {
		const row = event.target.closest("tr, .inline-related");
		const productSelect = row?.querySelector('select[id$="-product"]');
		if (productSelect?.value) {
			await updateLatestPrice(productSelect);
		}
	} else if (event.target?.id.endsWith("-purchase_order")) {
		const row = event.target.closest("tr, .inline-related");
		const productSelect = row?.querySelector('select[id$="-product"]');
		if (productSelect?.value) {
			await updatePurchasePriceFromOrder(productSelect);
		}
	}
});

document.addEventListener("input", (event) => {
	if (event.target?.id.endsWith("-customer_price") || event.target?.id.endsWith("-rrp")) {
		updateCustomerOrderPriceHighlight(event.target.closest("tr, .inline-related"));
	}
});

document.addEventListener("change", (event) => {
	if (event.target?.id.endsWith("-customer_price") || event.target?.id.endsWith("-rrp")) {
		updateCustomerOrderPriceHighlight(event.target.closest("tr, .inline-related"));
	}
});

function initializeCustomerOrderPriceHighlights() {
	document.querySelectorAll('input[id$="-customer_price"]').forEach((priceInput) => {
		updateCustomerOrderPriceHighlight(priceInput.closest("tr, .inline-related"));
	});
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", initializeCustomerOrderPriceHighlights);
} else {
	initializeCustomerOrderPriceHighlights();
}
