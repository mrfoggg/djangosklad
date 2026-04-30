document.addEventListener("change", async (event) => {
	if (event.target && event.target.id.endsWith("-product")) {
		const productSelect = event.target;
		const productId = productSelect.value;
		const supplierSelect = document.getElementById("id_supplier");
		const supplierId = supplierSelect ? supplierSelect.value : null;

		const row = productSelect.closest("tr, .inline-related");

		if (supplierId && productId) {
			const url = new URL("/documents/get-price/", window.location.origin);
			url.searchParams.append("supplier_id", supplierId);
			url.searchParams.append("product_id", productId);

			try {
				const response = await fetch(url);
				if (!response.ok) throw new Error(`Server error: ${response.status}`);

				const data = await response.json();
				const isDark = document.documentElement.classList.contains("dark");

				const swalConfig = {
					toast: true,
					position: "top-end",
					showConfirmButton: false,
					timer: 3000,
					timerProgressBar: true,
					background: isDark ? "#1f2937" : "#fff",
					color: isDark ? "#fff" : "#000",
				};

				const priceInput = row.querySelector('input[id$="-price_0"]');
				const currencySelect = row.querySelector('select[id$="-price_1"]');

				if (data.price) {
					if (priceInput) {
						priceInput.value = data.price;
						priceInput.classList.add("bg-green-100", "dark:bg-green-900/30");
						setTimeout(() => priceInput.classList.remove("bg-green-100", "dark:bg-green-900/30"), 800);
					}

					if (currencySelect) {
						currencySelect.value = data.currency;
						currencySelect.dispatchEvent(new Event("change", { bubbles: true }));
					}

					Swal.fire({
						...swalConfig,
						icon: "success",
						title: `Цена: ${data.price}`,
						html: `Док. №${data.doc_number} от ${data.doc_date}`,
					});
				} else {
					// Если цена не найдена — ставим 0
					if (priceInput) {
						priceInput.value = "0";
						priceInput.classList.add("bg-yellow-100", "dark:bg-yellow-900/30");
						setTimeout(() => priceInput.classList.remove("bg-yellow-100", "dark:bg-yellow-900/30"), 800);
					}

					Swal.fire({
						...swalConfig,
						icon: "info",
						title: "Цена не найдена",
						text: "Установлено значение 0",
					});
				}
			} catch (error) {
				console.error("Ошибка при загрузке цены:", error);
			}
		}
	}
});
