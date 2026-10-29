const { app } = require('electron')
app.whenReady().then(() => {
  console.log('App ready!')
  app.quit()
})
