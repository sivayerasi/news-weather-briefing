import aws_cdk as cdk

from infra.stack import BriefingStack

app = cdk.App()
BriefingStack(app, "news-weather-briefing")
app.synth()
