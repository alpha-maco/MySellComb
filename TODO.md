# TODO

- [ ] Validate and implement `Run fetch now` so an immediate TikTok run can arm the currently saved batch schedule and keep following that schedule until `Stop repeat crawl`.
- [x] Implement and test `Start repeat crawl` so it does not run immediately for TikTok; instead it should wait for the next saved batch schedule slot and continue until `Stop repeat crawl`.
