# Website deploy branch

This branch (`web`) exists only to publish the InvestorFlip web build to EAS Hosting.

It holds one file that the app branch does not have:
`.github/workflows/deploy-web.yml`

The app branch `feature/investorflip-v1` is never written to. The workflow checks
it out read-only, runs `expo export --platform web` inside `frontend/`, and deploys
the result with `eas deploy --prod`.

Production URL: https://investorflip.expo.app

## How to refresh the website after you update the app

Any push to this `web` branch starts a fresh build from the latest
`feature/investorflip-v1` code. The simplest way is to bump the counter below and
commit it to `web`.

redeploy counter: 1
