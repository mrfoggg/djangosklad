function filterOurBankAccounts() {
	const organizationSelect = document.getElementById("id_organization");
	const accountSelect = document.getElementById("id_our_bank_account");
	if (!organizationSelect || !accountSelect) {
		return;
	}

	const organizationId = organizationSelect.value;
	let selectedOptionIsAvailable = !accountSelect.value;
	let defaultOption = null;

	for (const option of accountSelect.options) {
		if (!option.value) {
			option.hidden = false;
			option.disabled = false;
			continue;
		}

		const isAvailable =
			Boolean(organizationId) && option.dataset.organizationId === organizationId;
		option.hidden = !isAvailable;
		option.disabled = !isAvailable;

		if (option.selected) {
			selectedOptionIsAvailable = isAvailable;
		}
		if (isAvailable && option.dataset.isDefault === "true") {
			defaultOption = option;
		}
	}

	if (!selectedOptionIsAvailable) {
		accountSelect.value = "";
	}
	if (!accountSelect.value && defaultOption) {
		accountSelect.value = defaultOption.value;
	}

	accountSelect.dispatchEvent(new Event("change", { bubbles: true }));
}

function filterContractorBankAccounts() {
	const contractorSelect = document.getElementById("id_contractor");
	const accountSelect = document.getElementById("id_contractor_bank_account");
	if (!contractorSelect || !accountSelect) {
		return;
	}

	const contractorId = contractorSelect.value;
	let selectedOptionIsAvailable = !accountSelect.value;
	let primaryOption = null;

	for (const option of accountSelect.options) {
		if (!option.value) {
			option.hidden = false;
			option.disabled = false;
			continue;
		}

		const isAvailable =
			Boolean(contractorId) && option.dataset.contractorId === contractorId;
		option.hidden = !isAvailable;
		option.disabled = !isAvailable;

		if (option.selected) {
			selectedOptionIsAvailable = isAvailable;
		}
		if (isAvailable && option.dataset.isPrimary === "true") {
			primaryOption = option;
		}
	}

	if (!selectedOptionIsAvailable) {
		accountSelect.value = "";
	}
	if (!accountSelect.value && primaryOption) {
		accountSelect.value = primaryOption.value;
	}

	accountSelect.dispatchEvent(new Event("change", { bubbles: true }));
}

document.addEventListener("change", (event) => {
	if (event.target?.id === "id_organization") {
		filterOurBankAccounts();
	} else if (event.target?.id === "id_contractor") {
		filterContractorBankAccounts();
	}
});

function initializeBankAccountFilters() {
	filterOurBankAccounts();
	filterContractorBankAccounts();
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", initializeBankAccountFilters);
} else {
	initializeBankAccountFilters();
}
