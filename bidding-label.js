(() => {
  const updateCard = card => {
    if (!card) return;
    const bidding = card.querySelector('input[data-field="bidding"]');
    const paid = card.querySelector('input[data-field="paid"]');
    const label = paid?.closest('.field')?.querySelector('label');
    if (!bidding || !paid || !label) return;

    const text = bidding.checked ? 'Current bid' : 'What I paid';
    label.textContent = text;
    paid.setAttribute('aria-label', text);
  };

  const updateAll = () => {
