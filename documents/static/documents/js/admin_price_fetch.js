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

async function updateLatestPrice(productSelect, { showNotification = true } = {}) {
	const productId = productSelect.value;
	const partnerSelect = document.getElementById("id_supplier") || document.getElementById("id_customer");
	const partnerId = partnerSelect?.value;
	const row = productSelect.closest("tr, .inline-related");
	const priceInput = row?.querySelector('input[id$="-price"]');

	if (!partnerId || !productId || !priceInput) {
		return { status: "skipped" };
	}

	const rowOrganizationSelect = row.querySelector('select[id$="-organization"]');
	const documentOrganizationSelect = document.getElementById("id_organization");
	const organizationId =
		rowOrganizationSelect?.value || documentOrganizationSelect?.value || null;

	const url = new URL("/documents/get-price/", window.location.origin);
	url.searchParams.append("supplier_id", partnerId);
	url.searchParams.append("product_id", productId);
	if (organizationId) {
		url.searchParams.append("organization_id", organizationId);
	}

	try {
		const response = await fetch(url);
		const data = await response.json();

		priceInput.value = data.price;
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

document.addEventListener("change", async (event) => {
	const isSupplierChanged = event.target?.id === "id_supplier";
	const isDocumentOrganizationChanged = event.target?.id === "id_organization";
	const isRowOrganizationChanged =
		event.target?.id.endsWith("-organization") && !isDocumentOrganizationChanged;

	if (isSupplierChanged || isDocumentOrganizationChanged) {
		const supplierId = document.getElementById("id_supplier")?.value;
		if (!supplierId || (isSupplierChanged && !event.target.value)) {
			return;
		}

		const productSelects = [...document.querySelectorAll('select[id$="-product"]')].filter(
			(productSelect) => {
				if (!productSelect.value) {
					return false;
				}

				if (!isDocumentOrganizationChanged) {
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
	} else if (isRowOrganizationChanged) {
		const row = event.target.closest("tr, .inline-related");
		const productSelect = row?.querySelector('select[id$="-product"]');
		if (productSelect?.value) {
			await updateLatestPrice(productSelect);
		}
	}
});
