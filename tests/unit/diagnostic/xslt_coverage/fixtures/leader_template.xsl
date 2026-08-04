<?xml version='1.0'?>
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:marc="http://www.loc.gov/MARC21/slim"
                xmlns:bf="http://id.loc.gov/ontologies/bibframe/">

  <xsl:template match="marc:leader" mode="instanceType">
    <xsl:variable name="kind" select="substring(.,7,1)"/>
    <xsl:variable name="issuance" select="substring(.,8,1)"/>
    <bf:Instance/>
    <bf:issuance/>
  </xsl:template>

</xsl:stylesheet>
