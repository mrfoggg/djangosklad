document.addEventListener("change", async (event) => {
	// Проверяем, что изменен селект товара
	if (event.target && event.target.id.endsWith("-product")) {
		const productSelect = event.target;
		const productId = productSelect.value;
		const supplierSelect = document.getElementById("id_supplier");
		const supplierId = supplierSelect ? supplierSelect.value : null;

		console.log(productId, supplierId);

		// Находим контейнер строки (tr или div инлайна)
		const row = productSelect.closest("tr, .inline-related");
		console.log(row);

		if (supplierId && productId) {
			const url = new URL("/documents/get-price/", window.location.origin);
			url.searchParams.append("supplier_id", supplierId);
			url.searchParams.append("product_id", productId);
			console.log(url);

			try {
				const response = await fetch(url);

				if (!response.ok) {
					throw new Error(`Server error: ${response.status}`);
				}

				const data = await response.json();
				console.log("response", data);

				if (data.price) {
					const priceInput = row.querySelector('input[id$="-price_0"]');
					const currencySelect = row.querySelector('select[id$="-price_1"]');

					if (priceInput) {
						priceInput.value = data.price;

						// Подсветка в стиле Unfold (Tailwind)
						priceInput.classList.add("bg-green-100", "dark:bg-green-900/30");
						setTimeout(() => {
							priceInput.classList.remove("bg-green-100", "dark:bg-green-900/30");
						}, 800);
					}

					if (currencySelect) {
						currencySelect.value = data.currency;
						// Триггерим событие, чтобы кастомные селекты Unfold подхватили изменения
						currencySelect.dispatchEvent(new Event("change", { bubbles: true }));
					}
				}
			} catch (error) {
				console.error("Ошибка при загрузке цены:", error);
			}
		}
	}
});
