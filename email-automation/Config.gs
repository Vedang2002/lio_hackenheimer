// Edit approved reply text here. Never put a Gmail password or API token in this file.
var LAUNCHGUARD_EMAIL_CONFIG = {
  expectedAccount: 'support.hackenheimer@gmail.com',
  mode: 'acknowledge', // acknowledge | approved_rules | draft
  inboxQuery: 'in:inbox -category:promotions -category:social',
  intervalMinutes: 1,
  messagesPerRun: 20,
  maxPagesPerRun: 5,
  maxWritesPerDay: 50,
  acknowledgment: 'Thank you for contacting Hackenheimer. We have received your message. This is an automatic acknowledgment; your request has not yet been reviewed by our team.\n\nHackenheimer Support',
  // In approved_rules mode, exactly one rule must match, otherwise hold for review.
  // Example: {id:'public-faq', matchAny:['your exact FAQ phrase'], reply:'Your approved public answer'}
  rules: []
};
